"""Job functions for scheduled data fetching and summarization."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.config import MARKET_HOURS, SYMBOL_ASSET_CLASS, SYMBOL_MARKET_MAP
from backend.db import get_connection
from backend.providers.fred import FredProvider
from backend.providers.twelve_data import TwelveDataProvider

logger = logging.getLogger(__name__)

_ET = ZoneInfo("US/Eastern")


# ---------------------------------------------------------------------------
# Market-hours helpers
# ---------------------------------------------------------------------------


def is_market_open(market: str, now_et: datetime) -> bool:
    """Check whether a market region is currently in its trading hours.

    Handles overnight sessions (e.g. Japan 20:00-02:00) where open > close.
    """
    if market == "24/7":
        return True

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

    Returns the number of rows inserted.
    """
    if not quotes:
        return 0

    rows = [
        (
            symbol,
            SYMBOL_ASSET_CLASS.get(symbol, "unknown"),
            data["price"],
            data.get("change_pct"),
            data.get("change_abs"),
            data.get("timestamp", ""),
        )
        for symbol, data in quotes.items()
        if "price" in data
    ]

    if not rows:
        return 0

    conn = await get_connection()
    try:
        await conn.executemany(
            """
            INSERT INTO market_snapshots
                (symbol, asset_class, price, change_pct, change_abs, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()
        return len(rows)
    except Exception:
        logger.exception("Failed to save %d quotes to database", len(rows))
        return 0
    finally:
        await conn.close()


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


async def generate_premarket_summary() -> None:
    """Generate the pre-market LLM summary (~8:00 AM ET).

    Computes regime classification, generates a narrative via the Claude API,
    and persists to the summaries table.
    """
    import json

    from backend.intelligence.regime import classify_regime
    from backend.intelligence.summary import generate_premarket

    logger.info("Pre-market summary job triggered")

    conn = await get_connection()
    try:
        regime = await classify_regime(conn)

        summary = await generate_premarket(regime)
        logger.info("Pre-market regime: %s | %s", regime["label"], regime["reason"])

        today = datetime.now(_ET).date().isoformat()

        await conn.execute(
            """
            INSERT INTO summaries
                (date, period, summary_text, regime_label, regime_reason,
                 regime_signals_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                today,
                "premarket",
                summary["summary_text"],
                regime["label"],
                regime["reason"],
                json.dumps(regime["signals"]),
            ),
        )
        await conn.commit()
    except Exception:
        logger.exception("Failed to generate premarket summary")
    finally:
        await conn.close()


async def generate_close_summary() -> None:
    """Generate the after-close LLM summary (~4:30 PM ET).

    Computes regime classification, generates a narrative via the Claude API,
    and persists to the summaries table.
    """
    import json

    from backend.intelligence.regime import classify_regime
    from backend.intelligence.summary import generate_close

    logger.info("After-close summary job triggered")

    conn = await get_connection()
    try:
        regime = await classify_regime(conn)
        logger.info("Regime: %s | %s", regime["label"], regime["reason"])

        summary = await generate_close(regime)

        today = datetime.now(_ET).date().isoformat()

        await conn.execute(
            """
            INSERT INTO summaries
                (date, period, summary_text, regime_label, regime_reason,
                 regime_signals_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                today,
                "close",
                summary["summary_text"],
                regime["label"],
                regime["reason"],
                json.dumps(regime["signals"]),
            ),
        )
        await conn.commit()
    except Exception:
        logger.exception("Failed to generate close summary")
    finally:
        await conn.close()
