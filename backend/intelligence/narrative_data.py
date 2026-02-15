"""Structured narrative data pipeline for LLM prompt generation.

Fetches technical indicators from Twelve Data, assembles enriched payloads
from database snapshots, and provides structured data for LLM narratives.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

from backend.config import TECHNICAL_SIGNAL_SYMBOLS, TWELVE_DATA_API_KEY
from backend.db import get_dialect, get_session
from backend.intelligence.regime import classify_regime

logger = logging.getLogger(__name__)

_ET = ZoneInfo("US/Eastern")
_BASE_URL = "https://api.twelvedata.com"
_TIMEOUT = 15.0

# ---------------------------------------------------------------------------
# Technical indicators — Twelve Data API
# ---------------------------------------------------------------------------


async def _fetch_indicator(
    client: httpx.AsyncClient,
    indicator: str,
    params: dict,
    symbol: str,
) -> float | None:
    """Fetch a single technical indicator value from Twelve Data."""
    try:
        resp = await client.get(f"{_BASE_URL}/{indicator}", params=params)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, Exception) as exc:
        logger.warning(
            "Indicator fetch failed %s/%s: %s", symbol, indicator, exc
        )
        return None

    if isinstance(data, dict) and data.get("status") == "error":
        logger.warning(
            "API error %s %s: %s", symbol, indicator, data.get("message")
        )
        return None

    values = data.get("values", [])
    if not values:
        return None

    val = values[0].get(indicator)
    if val is None:
        return None

    try:
        return float(val)
    except (ValueError, TypeError):
        return None


async def fetch_technical_signals() -> None:
    """Fetch RSI, ATR, SMA(50), SMA(200) for key symbols from Twelve Data.

    Makes 24 API calls (6 symbols x 4 indicators), staggered in batches
    of 6 with 12-second pauses to stay under the 55 credits/min rate limit.
    Stores results in the ``technical_signals`` table via upsert.
    """
    symbols = TECHNICAL_SIGNAL_SYMBOLS
    today = datetime.now(_ET).date().isoformat()

    # Build all (symbol, indicator, time_period) call specs
    call_specs: list[dict] = []
    for sym in symbols:
        for indicator, time_period in (
            ("rsi", "14"),
            ("atr", "14"),
            ("sma", "50"),
            ("sma", "200"),
        ):
            call_specs.append(
                {
                    "symbol": sym,
                    "indicator": indicator,
                    "time_period": time_period,
                    "params": {
                        "symbol": sym,
                        "interval": "1day",
                        "time_period": time_period,
                        "outputsize": "1",
                        "apikey": TWELVE_DATA_API_KEY,
                    },
                }
            )

    # Execute in batches of 6, pausing 12 s between batches
    # Key: (symbol, "rsi_14" | "atr_14" | "sma_50" | "sma_200") → float
    results: dict[tuple[str, str], float | None] = {}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for i in range(0, len(call_specs), 6):
            batch = call_specs[i : i + 6]
            tasks = [
                _fetch_indicator(
                    client,
                    spec["indicator"],
                    spec["params"],
                    spec["symbol"],
                )
                for spec in batch
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for spec, result in zip(batch, batch_results):
                key = f"{spec['indicator']}_{spec['time_period']}"
                if isinstance(result, Exception):
                    logger.warning(
                        "Technical fetch exception %s/%s: %s",
                        spec["symbol"],
                        key,
                        result,
                    )
                    results[(spec["symbol"], key)] = None
                else:
                    results[(spec["symbol"], key)] = result

            # Pause between batches (skip after the last batch)
            if i + 6 < len(call_specs):
                await asyncio.sleep(12)

    # Fetch current close prices from market_snapshots
    session = await get_session()
    try:
        placeholders = ", ".join(f":s{i}" for i in range(len(symbols)))
        params = {f"s{i}": sym for i, sym in enumerate(symbols)}
        price_result = await session.execute(
            text(f"""
                SELECT s.symbol, s.price
                FROM market_snapshots s
                INNER JOIN (
                    SELECT symbol, MAX(id) AS max_id
                    FROM market_snapshots
                    WHERE symbol IN ({placeholders})
                    GROUP BY symbol
                ) latest ON s.id = latest.max_id
            """),
            params,
        )
        prices = {
            row["symbol"]: row["price"]
            for row in price_result.mappings().all()
        }
    finally:
        await session.close()

    # Store results with upsert
    dialect = get_dialect()
    session = await get_session()
    try:
        for sym in symbols:
            row = {
                "symbol": sym,
                "date": today,
                "rsi_14": results.get((sym, "rsi_14")),
                "atr_14": results.get((sym, "atr_14")),
                "sma_50": results.get((sym, "sma_50")),
                "sma_200": results.get((sym, "sma_200")),
                "close": prices.get(sym),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if dialect == "postgresql":
                await session.execute(
                    text("""
                        INSERT INTO technical_signals
                            (symbol, date, rsi_14, atr_14, sma_50, sma_200,
                             close, created_at)
                        VALUES
                            (:symbol, :date, :rsi_14, :atr_14, :sma_50,
                             :sma_200, :close, :created_at)
                        ON CONFLICT (symbol, date) DO UPDATE SET
                            rsi_14 = EXCLUDED.rsi_14,
                            atr_14 = EXCLUDED.atr_14,
                            sma_50 = EXCLUDED.sma_50,
                            sma_200 = EXCLUDED.sma_200,
                            close = EXCLUDED.close,
                            created_at = EXCLUDED.created_at
                    """),
                    row,
                )
            else:
                await session.execute(
                    text("""
                        INSERT OR REPLACE INTO technical_signals
                            (symbol, date, rsi_14, atr_14, sma_50, sma_200,
                             close, created_at)
                        VALUES
                            (:symbol, :date, :rsi_14, :atr_14, :sma_50,
                             :sma_200, :close, :created_at)
                    """),
                    row,
                )

        await session.commit()
        logger.info(
            "Stored technical signals for %d symbols on %s",
            len(symbols),
            today,
        )
    except Exception:
        logger.exception("Failed to store technical signals")
        await session.rollback()
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Narrative payload assembly (DB reads only)
# ---------------------------------------------------------------------------


def _extract_rates(snapshots: dict[str, dict]) -> dict:
    """Pull FRED rate data from the latest snapshots."""
    dgs2 = snapshots.get("DGS2", {}).get("price")
    dgs10 = snapshots.get("DGS10", {}).get("price")
    spread = None
    if dgs2 is not None and dgs10 is not None:
        spread = round(dgs10 - dgs2, 4)

    return {
        "us_2y": dgs2,
        "us_10y": dgs10,
        "spread_2s10s": spread,
        "ig_spread": snapshots.get("BAMLC0A0CM", {}).get("price"),
        "hy_spread": snapshots.get("BAMLH0A0HYM2", {}).get("price"),
    }


def _build_regime_signals(
    regime: dict,
    snapshots: dict[str, dict],
    technicals: dict[str, dict],
    rates: dict,
) -> dict:
    """Map regime signal outputs to the structured payload format."""
    signal_map = {s["name"]: s for s in regime["signals"]}

    # --- sp500_trend ---
    spx_sig = signal_map.get("spx_trend", {})
    spy_tech = technicals.get("SPY", {})
    spy_snap = snapshots.get("SPY", {})
    sp500_dir = {"risk_on": "bullish", "risk_off": "bearish"}.get(
        spx_sig.get("direction", ""), "neutral"
    )
    detail_parts: list[str] = []
    if spy_tech.get("sma_50") and spy_snap.get("price"):
        pos = "above" if spy_snap["price"] > spy_tech["sma_50"] else "below"
        detail_parts.append(f"SPY {pos} 50-day MA")
    r7d = spy_snap.get("rolling_7d_change")
    if r7d is not None:
        detail_parts.append(f"7d change {r7d:+.1f}%")

    # --- vix ---
    vix_sig = signal_map.get("vix", {})
    vixy_snap = snapshots.get("VIXY", {})
    vix_dir = {"risk_on": "low", "risk_off": "high"}.get(
        vix_sig.get("direction", ""), "elevated"
    )

    # --- credit_spreads ---
    hy_sig = signal_map.get("hy_spread", {})
    credit_dir = {"risk_on": "tightening", "risk_off": "widening"}.get(
        hy_sig.get("direction", ""), "stable"
    )

    # --- yield_curve (computed from FRED) ---
    spread_2s10s = rates.get("spread_2s10s")
    if spread_2s10s is not None:
        if spread_2s10s < 0:
            yc_sig = "inverted"
        elif spread_2s10s < 0.25:
            yc_sig = "flat"
        else:
            yc_sig = "normal"
    else:
        yc_sig = "neutral"

    # --- usd_strength ---
    dxy_sig = signal_map.get("dxy", {})
    usd_dir = {"risk_off": "strengthening"}.get(
        dxy_sig.get("direction", ""), "stable"
    )
    uup_snap = snapshots.get("UUP", {})
    usd_detail_parts: list[str] = []
    uup_7d = uup_snap.get("rolling_7d_change")
    if uup_7d is not None:
        usd_detail_parts.append(f"UUP 7d change {uup_7d:+.1f}%")

    return {
        "sp500_trend": {
            "signal": sp500_dir,
            "detail": ", ".join(detail_parts) if detail_parts else spx_sig.get("detail", ""),
        },
        "vix": {
            "signal": vix_dir,
            "level": vixy_snap.get("price"),
        },
        "credit_spreads": {
            "signal": credit_dir,
            "ig": rates.get("ig_spread"),
            "hy": rates.get("hy_spread"),
        },
        "yield_curve": {
            "signal": yc_sig,
            "spread_2s10s": spread_2s10s,
        },
        "usd_strength": {
            "signal": usd_dir,
            "detail": ", ".join(usd_detail_parts) if usd_detail_parts else dxy_sig.get("detail", ""),
        },
    }


def _compute_confidence(regime: dict) -> str:
    """Describe directional alignment of the 5 regime signals."""
    risk_on = sum(
        1 for s in regime["signals"] if s["direction"] == "risk_on"
    )
    risk_off = sum(
        1 for s in regime["signals"] if s["direction"] == "risk_off"
    )

    if risk_off > risk_on:
        return f"{risk_off} of 5 signals bearish"
    if risk_on > risk_off:
        return f"{risk_on} of 5 signals bullish"
    if risk_on == risk_off and risk_on > 0:
        return f"{risk_on} bullish vs {risk_off} bearish \u2014 mixed"
    return "no strong directional signal"


def _build_asset_snapshot(
    snapshots: dict[str, dict],
    technicals: dict[str, dict],
) -> dict:
    """Build per-symbol asset data for the LLM payload."""
    # FRED series go in the rates section, not asset_snapshot
    skip = {"DGS2", "DGS10", "BAMLC0A0CM", "BAMLH0A0HYM2", "SPREAD_2S10S"}

    result: dict[str, dict] = {}
    for symbol, snap in snapshots.items():
        if symbol in skip:
            continue

        tech = technicals.get(symbol, {})
        price = snap.get("price")

        # SMA position checks
        above_sma50 = None
        above_sma200 = None
        if price is not None:
            if tech.get("sma_50") is not None:
                above_sma50 = price > tech["sma_50"]
            if tech.get("sma_200") is not None:
                above_sma200 = price > tech["sma_200"]

        # Volume vs average (both must be present and non-zero)
        avg_vol = snap.get("average_volume")
        volume_vs_avg = None  # current volume not stored in snapshots

        result[symbol] = {
            "price": price,
            "change_pct": snap.get("change_pct"),
            "pre_market_change_pct": None,
            "volume_vs_avg": volume_vs_avg,
            "rsi_14": tech.get("rsi_14"),
            "atr_14": tech.get("atr_14"),
            "above_sma50": above_sma50,
            "above_sma200": above_sma200,
            "distance_from_52w_high_pct": snap.get("fifty_two_week_high_change_pct"),
            "distance_from_52w_low_pct": snap.get("fifty_two_week_low_change_pct"),
            "rolling_7d_change": snap.get("rolling_7d_change"),
        }

    return result


async def assemble_narrative_payload(narrative_type: str) -> dict:
    """Assemble a structured dict for LLM narrative generation.

    Reads exclusively from the database — no external API calls.

    Args:
        narrative_type: ``"pre_market"`` or ``"after_close"``.

    Returns:
        Structured dict with regime, asset snapshot, rates, and previous
        narrative context.
    """
    now_et = datetime.now(_ET)

    session = await get_session()
    try:
        # 1. Classify regime
        regime = await classify_regime(session)

        # 2. Fetch latest market snapshots (including enriched columns)
        snap_result = await session.execute(
            text("""
                SELECT s.symbol, s.price, s.change_pct, s.change_abs,
                       s.timestamp, s.average_volume,
                       s.fifty_two_week_high, s.fifty_two_week_low,
                       s.fifty_two_week_high_change_pct,
                       s.fifty_two_week_low_change_pct,
                       s.rolling_1d_change, s.rolling_7d_change
                FROM market_snapshots s
                INNER JOIN (
                    SELECT symbol, MAX(id) AS max_id
                    FROM market_snapshots GROUP BY symbol
                ) latest ON s.id = latest.max_id
            """)
        )
        snapshots = {
            row["symbol"]: dict(row) for row in snap_result.mappings().all()
        }

        # 3. Fetch most recent technical signals
        tech_result = await session.execute(
            text("""
                SELECT symbol, rsi_14, atr_14, sma_50, sma_200, close
                FROM technical_signals
                WHERE date = (SELECT MAX(date) FROM technical_signals)
            """)
        )
        technicals = {
            row["symbol"]: dict(row) for row in tech_result.mappings().all()
        }

        # 4. Previous narrative (most recent from archive)
        prev_result = await session.execute(
            text("""
                SELECT date, narrative_type, regime_label, narrative_text
                FROM narrative_archive
                ORDER BY id DESC
                LIMIT 1
            """)
        )
        prev_row = prev_result.mappings().first()
    finally:
        await session.close()

    # 5. Derived data
    rates = _extract_rates(snapshots)
    regime_signals = _build_regime_signals(regime, snapshots, technicals, rates)
    confidence = _compute_confidence(regime)
    asset_snapshot = _build_asset_snapshot(snapshots, technicals)

    # Regime change detection
    changed_since_last = False
    previous_label = None
    if prev_row:
        previous_label = prev_row["regime_label"]
        changed_since_last = regime["label"] != previous_label

    # Data freshness
    if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30):
        freshness = "pre_market"
    elif now_et.hour < 16:
        freshness = "live"
    else:
        freshness = "close"

    # Previous narrative context
    previous_narrative = None
    if prev_row:
        narrative_text = prev_row["narrative_text"]
        sentences = narrative_text.split(". ")
        summary = ". ".join(sentences[:3])
        if not summary.endswith("."):
            summary += "."
        previous_narrative = {
            "date": prev_row["date"],
            "type": prev_row["narrative_type"],
            "regime_label": prev_row["regime_label"],
            "summary": summary,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "narrative_type": narrative_type,
        "data_freshness": freshness,
        "regime": {
            "label": regime["label"],
            "changed_since_last": changed_since_last,
            "previous_label": previous_label,
            "signals": regime_signals,
            "confidence": confidence,
        },
        "asset_snapshot": asset_snapshot,
        "rates": rates,
        "previous_narrative": previous_narrative,
    }
