"""Rule-based market regime classification.

Evaluates five signals against configurable thresholds and classifies
the current market as RISK-ON, RISK-OFF, or MIXED.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TypedDict

import aiosqlite

from backend.config import REGIME_THRESHOLDS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class Signal(TypedDict):
    """One regime signal evaluation."""

    name: str        # e.g. "spx_trend"
    direction: str   # "risk_on", "risk_off", or "neutral"
    detail: str      # human-readable snippet, e.g. "S&P above 20-day MA (5100 vs 5020)"


class RegimeResult(TypedDict):
    """Full regime classification output."""

    label: str            # "RISK-ON", "RISK-OFF", or "MIXED"
    reason: str           # one-line summary
    signals: list[Signal]
    timestamp: str        # ISO-8601


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _get_latest_snapshot(
    conn: aiosqlite.Connection,
    symbol: str,
) -> dict | None:
    """Return the most recent market_snapshots row for *symbol*."""
    cursor = await conn.execute(
        """
        SELECT price, change_pct, change_abs, timestamp
        FROM market_snapshots
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (symbol,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _get_snapshot_n_days_ago(
    conn: aiosqlite.Connection,
    symbol: str,
    days_back: int,
) -> dict | None:
    """Return the closest snapshot to *days_back* days in the past."""
    target = datetime.now(timezone.utc) - timedelta(days=days_back)
    cursor = await conn.execute(
        """
        SELECT price, change_pct, change_abs, timestamp
        FROM market_snapshots
        WHERE symbol = ? AND timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (symbol, target.isoformat()),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _compute_sma(
    conn: aiosqlite.Connection,
    symbol: str,
    period: int,
) -> float | None:
    """Compute a simple moving average over the last *period* trading days.

    Groups intraday snapshots by calendar date, takes the latest price per
    day, then averages the most recent *period* days.  Returns ``None`` when
    fewer than *period* days of data are available.
    """
    cursor = await conn.execute(
        """
        SELECT date(timestamp) AS day, price
        FROM market_snapshots
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT 500
        """,
        (symbol,),
    )
    rows = await cursor.fetchall()
    if not rows:
        return None

    # Keep only the first (latest) price encountered for each day.
    daily_closes: dict[str, float] = {}
    for row in rows:
        day = row["day"]
        if day not in daily_closes:
            daily_closes[day] = row["price"]

    prices = list(daily_closes.values())[:period]
    if len(prices) < period:
        return None

    return sum(prices) / len(prices)


# ---------------------------------------------------------------------------
# Signal evaluators
# ---------------------------------------------------------------------------


async def _eval_spx_trend(conn: aiosqlite.Connection) -> Signal:
    """S&P 500 price vs its N-day simple moving average."""
    period = int(REGIME_THRESHOLDS["spx_ma_period"])
    latest = await _get_latest_snapshot(conn, "SPX")
    if latest is None:
        return Signal(name="spx_trend", direction="neutral", detail="S&P 500 data unavailable")

    sma = await _compute_sma(conn, "SPX", period)
    if sma is None:
        return Signal(name="spx_trend", direction="neutral", detail=f"insufficient history for {period}-day MA")

    price = latest["price"]
    if price > sma:
        return Signal(
            name="spx_trend",
            direction="risk_on",
            detail=f"S&P above {period}-day MA ({price:.0f} vs {sma:.0f})",
        )
    return Signal(
        name="spx_trend",
        direction="risk_off",
        detail=f"S&P below {period}-day MA ({price:.0f} vs {sma:.0f})",
    )


async def _eval_vix(conn: aiosqlite.Connection) -> Signal:
    """VIX level check against risk-on / risk-off thresholds."""
    latest = await _get_latest_snapshot(conn, "VIX")
    if latest is None:
        return Signal(name="vix", direction="neutral", detail="VIX data unavailable")

    vix = latest["price"]
    if vix < REGIME_THRESHOLDS["vix_risk_on"]:
        return Signal(name="vix", direction="risk_on", detail=f"VIX low ({vix:.1f})")
    if vix > REGIME_THRESHOLDS["vix_risk_off"]:
        return Signal(name="vix", direction="risk_off", detail=f"VIX elevated ({vix:.1f})")
    return Signal(name="vix", direction="neutral", detail=f"VIX neutral ({vix:.1f})")


async def _eval_hy_spread(conn: aiosqlite.Connection) -> Signal:
    """HY credit spread level and week-over-week trend."""
    latest = await _get_latest_snapshot(conn, "BAMLH0A0HYM2")
    if latest is None:
        return Signal(name="hy_spread", direction="neutral", detail="HY spread data unavailable")

    spread = latest["price"]

    # Absolute level check first
    if spread > REGIME_THRESHOLDS["hy_spread_risk_off"]:
        return Signal(
            name="hy_spread",
            direction="risk_off",
            detail=f"HY spread elevated ({spread:.2f}%)",
        )

    # Week-over-week trend
    week_ago = await _get_snapshot_n_days_ago(conn, "BAMLH0A0HYM2", 7)
    if week_ago is not None:
        change_bps = (spread - week_ago["price"]) * 100
        if change_bps > REGIME_THRESHOLDS["hy_spread_widening_bps"]:
            return Signal(
                name="hy_spread",
                direction="risk_off",
                detail=f"HY spreads widening (+{change_bps:.0f} bps WoW)",
            )

    # Low level + stable/tightening → risk-on
    if spread < REGIME_THRESHOLDS["hy_spread_risk_on"]:
        return Signal(
            name="hy_spread",
            direction="risk_on",
            detail=f"HY spreads tight ({spread:.2f}%)",
        )

    return Signal(name="hy_spread", direction="neutral", detail=f"HY spread neutral ({spread:.2f}%)")


async def _eval_dxy(conn: aiosqlite.Connection) -> Signal:
    """DXY spike detection (asymmetric — only flags risk-off)."""
    latest = await _get_latest_snapshot(conn, "DXY")
    if latest is None or latest.get("change_pct") is None:
        return Signal(name="dxy", direction="neutral", detail="DXY data unavailable")

    change = latest["change_pct"]
    if change > REGIME_THRESHOLDS["dxy_spike_pct"]:
        return Signal(
            name="dxy",
            direction="risk_off",
            detail=f"dollar spiking (+{change:.1f}%)",
        )
    return Signal(name="dxy", direction="neutral", detail=f"DXY stable ({change:+.1f}%)")


async def _eval_gold_vs_equities(conn: aiosqlite.Connection) -> Signal:
    """Gold outperforming equities (asymmetric — only flags risk-off).

    Requires gold to be up more than ``gold_safe_haven_pct`` AND
    outperforming S&P to filter out noise on flat days.
    """
    gold = await _get_latest_snapshot(conn, "XAU")
    spx = await _get_latest_snapshot(conn, "SPX")
    if gold is None or spx is None:
        return Signal(name="gold_vs_equities", direction="neutral", detail="gold/equity data unavailable")

    gold_pct = gold.get("change_pct")
    spx_pct = spx.get("change_pct")
    if gold_pct is None or spx_pct is None:
        return Signal(name="gold_vs_equities", direction="neutral", detail="gold/equity change data unavailable")

    threshold = REGIME_THRESHOLDS["gold_safe_haven_pct"]
    if gold_pct > threshold and gold_pct > spx_pct:
        return Signal(
            name="gold_vs_equities",
            direction="risk_off",
            detail=f"gold outperforming equities (+{gold_pct:.1f}% vs +{spx_pct:.1f}%)",
        )
    return Signal(name="gold_vs_equities", direction="neutral", detail="gold not outperforming")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _classify(signals: list[Signal]) -> str:
    """Determine regime label from signal directions.

    * RISK-ON  — at least 2 risk-on signals AND zero risk-off.
    * RISK-OFF — at least 2 risk-off signals.
    * MIXED    — everything else (conflicts or sparse data).
    """
    risk_on = sum(1 for s in signals if s["direction"] == "risk_on")
    risk_off = sum(1 for s in signals if s["direction"] == "risk_off")

    if risk_off >= 2:
        return "RISK-OFF"
    if risk_on >= 2 and risk_off == 0:
        return "RISK-ON"
    return "MIXED"


def _build_reason(signals: list[Signal]) -> str:
    """Join non-neutral signal details into a one-line reason string."""
    parts = [s["detail"] for s in signals if s["direction"] != "neutral"]
    if not parts:
        return "Insufficient data for regime classification"
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def classify_regime(conn: aiosqlite.Connection) -> RegimeResult:
    """Classify the current market regime from latest snapshots.

    Evaluates five signals (S&P trend, VIX, HY spread, DXY, gold vs
    equities), aggregates them, and returns a labelled result.
    """
    signals = [
        await _eval_spx_trend(conn),
        await _eval_vix(conn),
        await _eval_hy_spread(conn),
        await _eval_dxy(conn),
        await _eval_gold_vs_equities(conn),
    ]

    label = _classify(signals)
    reason = _build_reason(signals)

    return RegimeResult(
        label=label,
        reason=reason,
        signals=signals,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
