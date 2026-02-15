"""Job functions for scheduled data fetching and summarization."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import MARKET_HOURS, SYMBOL_ASSET_CLASS, SYMBOL_MARKET_MAP
from backend.db import get_session
from backend.providers.fred import FredProvider
from backend.providers.twelve_data import TwelveDataProvider

logger = logging.getLogger(__name__)

_ET = ZoneInfo("US/Eastern")


# ---------------------------------------------------------------------------
# Market-hours helpers
# ---------------------------------------------------------------------------


def is_market_open(market: str, now_et: datetime) -> bool:
    """Check whether a market region is currently in its trading hours.

    Returns ``False`` on weekends for all non-crypto markets.
    Handles overnight sessions (e.g. Japan 20:00-02:00) where open > close.
    """
    if market == "24/7":
        return True

    # Saturday = 5, Sunday = 6 — no equity/FX/commodity market trades.
    if now_et.weekday() >= 5:
        return False

    hours = MARKET_HOURS.get(market)
    if hours is None:
        logger.warning("Unknown market region: %s", market)
        return False

    current_time = now_et.time()
    open_time = datetime.strptime(hours["open"], "%H:%M").time()
    close_time = datetime.strptime(hours["close"], "%H:%M").time()

    if open_time < close_time:
        # Normal hours (e.g. US 09:30-16:00)
        return open_time <= current_time <= close_time
    else:
        # Overnight session (e.g. Japan 20:00-02:00)
        return current_time >= open_time or current_time <= close_time


def get_active_symbols(now_et: datetime) -> list[str]:
    """Return Twelve Data symbols whose markets are currently open."""
    return [
        symbol
        for symbol, market in SYMBOL_MARKET_MAP.items()
        if is_market_open(market, now_et)
    ]


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------


async def save_quotes(quotes: dict[str, dict]) -> int:
    """Insert quote data into the market_snapshots table.

    Stores core price data alongside enriched fields (52-week, average
    volume, rolling changes) when available.  Returns the number of rows
    inserted.
    """
    if not quotes:
        return 0

    rows = [
        {
            "symbol": symbol,
            "asset_class": SYMBOL_ASSET_CLASS.get(symbol, "unknown"),
            "price": data["price"],
            "change_pct": data.get("change_pct"),
            "change_abs": data.get("change_abs"),
            "timestamp": data.get("timestamp", ""),
            "average_volume": data.get("average_volume"),
            "fifty_two_week_high": data.get("fifty_two_week_high"),
            "fifty_two_week_low": data.get("fifty_two_week_low"),
            "fifty_two_week_high_change_pct": data.get("fifty_two_week_high_change_pct"),
            "fifty_two_week_low_change_pct": data.get("fifty_two_week_low_change_pct"),
            "rolling_1d_change": data.get("rolling_1d_change"),
            "rolling_7d_change": data.get("rolling_7d_change"),
        }
        for symbol, data in quotes.items()
        if "price" in data
    ]

    if not rows:
        return 0

    session = await get_session()
    try:
        await session.execute(
            text("""
                INSERT INTO market_snapshots
                    (symbol, asset_class, price, change_pct, change_abs, timestamp,
                     average_volume, fifty_two_week_high, fifty_two_week_low,
                     fifty_two_week_high_change_pct, fifty_two_week_low_change_pct,
                     rolling_1d_change, rolling_7d_change)
                VALUES (:symbol, :asset_class, :price, :change_pct, :change_abs, :timestamp,
                        :average_volume, :fifty_two_week_high, :fifty_two_week_low,
                        :fifty_two_week_high_change_pct, :fifty_two_week_low_change_pct,
                        :rolling_1d_change, :rolling_7d_change)
            """),
            rows,
        )
        await session.commit()
        return len(rows)
    except Exception:
        logger.exception("Failed to save %d quotes to database", len(rows))
        await session.rollback()
        return 0
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Narrative archive helpers
# ---------------------------------------------------------------------------


async def _compute_movers_snapshot(session: AsyncSession) -> dict:
    """Query latest snapshots and group into up/down movers by change_pct."""
    result = await session.execute(
        text("""
            SELECT s.symbol, s.change_pct
            FROM market_snapshots s
            INNER JOIN (
                SELECT symbol, MAX(id) AS max_id
                FROM market_snapshots GROUP BY symbol
            ) latest ON s.id = latest.max_id
            WHERE s.change_pct IS NOT NULL
        """)
    )
    rows = result.mappings().all()

    up = sorted(
        [{"symbol": r["symbol"], "change_pct": r["change_pct"]} for r in rows if r["change_pct"] > 0],
        key=lambda x: x["change_pct"],
        reverse=True,
    )
    down = sorted(
        [{"symbol": r["symbol"], "change_pct": r["change_pct"]} for r in rows if r["change_pct"] < 0],
        key=lambda x: x["change_pct"],
    )

    return {"up": up, "down": down}


async def _archive_narrative(
    session: AsyncSession,
    narrative_type: str,
    regime: dict,
    summary_text: str,
    movers: dict,
) -> None:
    """Write a narrative to the narrative_archive table."""
    today = datetime.now(_ET).date().isoformat()
    await session.execute(
        text("""
            INSERT INTO narrative_archive
                (timestamp, date, narrative_type, regime_label,
                 narrative_text, signal_inputs, movers_snapshot)
            VALUES (:timestamp, :date, :narrative_type, :regime_label,
                    :narrative_text, :signal_inputs, :movers_snapshot)
        """),
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "date": today,
            "narrative_type": narrative_type,
            "regime_label": regime["label"],
            "narrative_text": summary_text,
            "signal_inputs": json.dumps(regime.get("signals")),
            "movers_snapshot": json.dumps(movers),
        },
    )


# ---------------------------------------------------------------------------
# Scheduled job functions
# ---------------------------------------------------------------------------


async def fetch_twelve_data_quotes(provider: TwelveDataProvider) -> None:
    """Fetch quotes for Twelve Data symbols whose markets are open.

    Called every 10 minutes by the scheduler. Determines which markets are
    open, batch-fetches the relevant symbols, and saves to the database.
    """
    now_et = datetime.now(_ET)
    active_symbols = get_active_symbols(now_et)

    if not active_symbols:
        logger.debug(
            "No markets open at %s ET -- skipping Twelve Data fetch",
            now_et.strftime("%H:%M"),
        )
        return

    logger.info(
        "Fetching %d Twelve Data symbols (markets open at %s ET)",
        len(active_symbols),
        now_et.strftime("%H:%M"),
    )

    quotes = await provider.get_quotes_for_symbols(active_symbols)

    if not quotes:
        logger.warning(
            "Twelve Data returned no quotes for %d symbols", len(active_symbols)
        )
        return

    saved = await save_quotes(quotes)
    logger.info(
        "Twelve Data: fetched %d/%d quotes, saved %d rows",
        len(quotes),
        len(active_symbols),
        saved,
    )


async def fetch_fred_quotes(provider: FredProvider) -> None:
    """Fetch all FRED series (rates + credit spreads) and save to database.

    Called once daily at ~3:30 PM ET by the scheduler.
    """
    logger.info("Fetching FRED quotes")

    quotes = await provider.get_all_quotes()

    if not quotes:
        logger.warning("FRED returned no quotes")
        return

    saved = await save_quotes(quotes)
    logger.info("FRED: fetched %d quotes, saved %d rows", len(quotes), saved)


async def _save_summary_and_archive(
    period: str,
    narrative_type: str,
    regime: dict,
    summary_text: str,
) -> None:
    """Save to both summaries and narrative_archive tables.

    The summaries INSERT is committed first so a failure in the archive
    write cannot roll back the summary.  Each write uses its own session.
    """
    today = datetime.now(_ET).date().isoformat()

    # 1. Write to summaries (must not be lost)
    session = await get_session()
    try:
        await session.execute(
            text("""
                INSERT INTO summaries
                    (date, period, summary_text, regime_label, regime_reason,
                     regime_signals_json)
                VALUES (:date, :period, :summary_text, :regime_label, :regime_reason,
                        :regime_signals_json)
            """),
            {
                "date": today,
                "period": period,
                "summary_text": summary_text,
                "regime_label": regime["label"],
                "regime_reason": regime["reason"],
                "regime_signals_json": json.dumps(regime["signals"]),
            },
        )
        await session.commit()
        logger.info("Saved %s summary for %s", period, today)
    except Exception:
        logger.exception("Failed to save %s summary for %s", period, today)
        await session.rollback()
        return  # no point archiving if the summary itself failed
    finally:
        await session.close()

    # 2. Write to narrative_archive (separate transaction)
    session = await get_session()
    try:
        movers = await _compute_movers_snapshot(session)
        await _archive_narrative(session, narrative_type, regime, summary_text, movers)
        await session.commit()
        logger.info("Archived %s narrative for %s", narrative_type, today)
    except Exception:
        logger.exception("Failed to archive %s narrative for %s", narrative_type, today)
        await session.rollback()
    finally:
        await session.close()


async def fetch_premarket_quotes(provider: TwelveDataProvider) -> None:
    """Fetch quotes for all symbols before market open.

    Runs at 7:45 AM ET to ensure fresh pre-market data (including
    extended-hours quotes for US equities via prepost=true) before
    the morning narrative is generated.
    """
    logger.info("Pre-market quote refresh triggered")

    quotes = await provider.get_all_quotes()
    if not quotes:
        logger.warning("Pre-market refresh returned no quotes")
        return

    saved = await save_quotes(quotes)
    logger.info("Pre-market refresh: fetched %d quotes, saved %d rows", len(quotes), saved)


async def generate_premarket_summary() -> None:
    """Generate the pre-market LLM summary (~9:45 AM ET).

    Assembles an enriched narrative payload from DB data, calls the
    Claude API with the structured prompt, and persists to both the
    summaries table and narrative_archive.
    """
    from backend.intelligence.narrative_data import assemble_narrative_payload
    from backend.intelligence.summary import generate_narrative

    logger.info("Pre-market summary job triggered")

    payload = await assemble_narrative_payload("pre_market")
    summary = await generate_narrative(payload)

    # Build a regime dict compatible with _save_summary_and_archive
    regime = {
        "label": payload["regime"]["label"],
        "reason": payload["regime"]["confidence"],
        "signals": payload["regime"]["signals"],
    }
    logger.info("Pre-market regime: %s | %s", regime["label"], regime["reason"])

    await _save_summary_and_archive("premarket", "pre_market", regime, summary["summary_text"])


async def generate_close_summary() -> None:
    """Generate the after-close LLM summary (~4:50 PM ET).

    Assembles an enriched narrative payload from DB data, calls the
    Claude API with the structured prompt, and persists to both the
    summaries table and narrative_archive.
    """
    from backend.intelligence.narrative_data import assemble_narrative_payload
    from backend.intelligence.summary import generate_narrative

    logger.info("After-close summary job triggered")

    payload = await assemble_narrative_payload("after_close")
    summary = await generate_narrative(payload)

    regime = {
        "label": payload["regime"]["label"],
        "reason": payload["regime"]["confidence"],
        "signals": payload["regime"]["signals"],
    }
    logger.info("Regime: %s | %s", regime["label"], regime["reason"])

    await _save_summary_and_archive("close", "after_close", regime, summary["summary_text"])
