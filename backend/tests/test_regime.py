"""Tests for the rule-based regime classification module.

Covers:
- Each signal evaluator in isolation (SPX trend, VIXY, HY spread, UUP, gold)
- Aggregation logic (_classify)
- Reason builder
- Full classify_regime integration with various market scenarios
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from backend.db import close_db, get_session, init_db
from backend.intelligence.regime import (
    _build_reason,
    _classify,
    _eval_dxy,
    _eval_gold_vs_equities,
    _eval_hy_spread,
    _eval_spx_trend,
    _eval_vix,
    classify_regime,
)

_ET = ZoneInfo("US/Eastern")

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    """Point the database at a temporary SQLite file for every test."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    asyncio.get_event_loop().run_until_complete(init_db(db_url))
    yield
    asyncio.get_event_loop().run_until_complete(close_db())


async def _insert_snapshot(
    symbol: str,
    price: float,
    change_pct: float | None = 0.0,
    timestamp: str | None = None,
) -> None:
    """Insert one row into market_snapshots."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    session = await get_session()
    try:
        await session.execute(
            text("""
                INSERT INTO market_snapshots
                    (symbol, asset_class, price, change_pct, change_abs, timestamp)
                VALUES (:symbol, :asset_class, :price, :change_pct, :change_abs, :timestamp)
            """),
            {
                "symbol": symbol,
                "asset_class": "test",
                "price": price,
                "change_pct": change_pct,
                "change_abs": 0.0,
                "timestamp": timestamp,
            },
        )
        await session.commit()
    finally:
        await session.close()


async def _seed_spx_history(base_price: float, days: int) -> None:
    """Insert one SPX snapshot per day for *days* trading days."""
    now = datetime.now(timezone.utc)
    for i in range(days):
        ts = (now - timedelta(days=i + 1)).isoformat()
        await _insert_snapshot("SPY", base_price, timestamp=ts)


# ---------------------------------------------------------------------------
# _eval_spx_trend
# ---------------------------------------------------------------------------


class TestSpxTrend:
    @pytest.mark.asyncio
    async def test_above_ma(self):
        await _seed_spx_history(5000.0, 20)
        await _insert_snapshot("SPY", 5200.0)

        session = await get_session()
        try:
            sig = await _eval_spx_trend(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_on"
        assert "above" in sig["detail"]

    @pytest.mark.asyncio
    async def test_below_ma(self):
        await _seed_spx_history(5000.0, 20)
        await _insert_snapshot("SPY", 4800.0)

        session = await get_session()
        try:
            sig = await _eval_spx_trend(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_off"
        assert "below" in sig["detail"]

    @pytest.mark.asyncio
    async def test_insufficient_history(self):
        await _seed_spx_history(5000.0, 10)
        await _insert_snapshot("SPY", 5100.0)

        session = await get_session()
        try:
            sig = await _eval_spx_trend(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self):
        session = await get_session()
        try:
            sig = await _eval_spx_trend(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"


# ---------------------------------------------------------------------------
# _eval_vix
# ---------------------------------------------------------------------------


class TestVix:
    @pytest.mark.asyncio
    async def test_vixy_falling_risk_on(self):
        await _insert_snapshot("VIXY", 24.0, change_pct=-7.0)

        session = await get_session()
        try:
            sig = await _eval_vix(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_on"
        assert "falling" in sig["detail"]

    @pytest.mark.asyncio
    async def test_vixy_spiking_risk_off(self):
        await _insert_snapshot("VIXY", 30.0, change_pct=8.0)

        session = await get_session()
        try:
            sig = await _eval_vix(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_off"
        assert "spiking" in sig["detail"]

    @pytest.mark.asyncio
    async def test_neutral_range(self):
        await _insert_snapshot("VIXY", 25.0, change_pct=-2.0)

        session = await get_session()
        try:
            sig = await _eval_vix(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self):
        session = await get_session()
        try:
            sig = await _eval_vix(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"


# ---------------------------------------------------------------------------
# _eval_hy_spread
# ---------------------------------------------------------------------------


class TestHySpread:
    @pytest.mark.asyncio
    async def test_elevated_level_risk_off(self):
        await _insert_snapshot("BAMLH0A0HYM2", 5.5)

        session = await get_session()
        try:
            sig = await _eval_hy_spread(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_off"
        assert "elevated" in sig["detail"]

    @pytest.mark.asyncio
    async def test_widening_wow_risk_off(self):
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        await _insert_snapshot("BAMLH0A0HYM2", 3.50, timestamp=week_ago)
        await _insert_snapshot("BAMLH0A0HYM2", 3.65)  # +15 bps

        session = await get_session()
        try:
            sig = await _eval_hy_spread(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_off"
        assert "widening" in sig["detail"]

    @pytest.mark.asyncio
    async def test_tight_and_stable_risk_on(self):
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        await _insert_snapshot("BAMLH0A0HYM2", 3.30, timestamp=week_ago)
        await _insert_snapshot("BAMLH0A0HYM2", 3.25)  # tightening

        session = await get_session()
        try:
            sig = await _eval_hy_spread(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_on"
        assert "tight" in sig["detail"]

    @pytest.mark.asyncio
    async def test_no_history_falls_back_to_level(self):
        """No WoW data but low spread → still risk-on."""
        await _insert_snapshot("BAMLH0A0HYM2", 3.2)

        session = await get_session()
        try:
            sig = await _eval_hy_spread(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_on"

    @pytest.mark.asyncio
    async def test_neutral_zone(self):
        """Spread in neutral range, stable WoW."""
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        await _insert_snapshot("BAMLH0A0HYM2", 4.0, timestamp=week_ago)
        await _insert_snapshot("BAMLH0A0HYM2", 4.05)  # +5 bps, below threshold

        session = await get_session()
        try:
            sig = await _eval_hy_spread(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self):
        session = await get_session()
        try:
            sig = await _eval_hy_spread(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"


# ---------------------------------------------------------------------------
# _eval_dxy
# ---------------------------------------------------------------------------


class TestDxy:
    @pytest.mark.asyncio
    async def test_spiking_risk_off(self):
        await _insert_snapshot("UUP", 27.5, change_pct=1.5)

        session = await get_session()
        try:
            sig = await _eval_dxy(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_off"
        assert "spiking" in sig["detail"]

    @pytest.mark.asyncio
    async def test_stable_neutral(self):
        await _insert_snapshot("UUP", 26.8, change_pct=0.2)

        session = await get_session()
        try:
            sig = await _eval_dxy(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self):
        session = await get_session()
        try:
            sig = await _eval_dxy(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"


# ---------------------------------------------------------------------------
# _eval_gold_vs_equities
# ---------------------------------------------------------------------------


class TestGoldVsEquities:
    @pytest.mark.asyncio
    async def test_gold_outperforming_risk_off(self):
        await _insert_snapshot("GLD", 2100.0, change_pct=2.0)
        await _insert_snapshot("SPY", 5000.0, change_pct=0.5)

        session = await get_session()
        try:
            sig = await _eval_gold_vs_equities(session)
        finally:
            await session.close()

        assert sig["direction"] == "risk_off"
        assert "outperforming" in sig["detail"]

    @pytest.mark.asyncio
    async def test_gold_up_but_below_threshold(self):
        """Gold beating S&P but not up enough to trigger safe-haven."""
        await _insert_snapshot("GLD", 2100.0, change_pct=0.5)
        await _insert_snapshot("SPY", 5000.0, change_pct=0.1)

        session = await get_session()
        try:
            sig = await _eval_gold_vs_equities(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_spx_outperforming_neutral(self):
        await _insert_snapshot("GLD", 2100.0, change_pct=0.5)
        await _insert_snapshot("SPY", 5000.0, change_pct=1.5)

        session = await get_session()
        try:
            sig = await _eval_gold_vs_equities(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self):
        session = await get_session()
        try:
            sig = await _eval_gold_vs_equities(session)
        finally:
            await session.close()

        assert sig["direction"] == "neutral"


# ---------------------------------------------------------------------------
# _classify (pure function, no DB)
# ---------------------------------------------------------------------------


class TestClassify:
    def test_risk_on_two_signals(self):
        signals = [
            {"name": "a", "direction": "risk_on", "detail": ""},
            {"name": "b", "direction": "risk_on", "detail": ""},
            {"name": "c", "direction": "neutral", "detail": ""},
        ]
        assert _classify(signals) == "RISK-ON"

    def test_risk_on_blocked_by_risk_off(self):
        signals = [
            {"name": "a", "direction": "risk_on", "detail": ""},
            {"name": "b", "direction": "risk_on", "detail": ""},
            {"name": "c", "direction": "risk_off", "detail": ""},
        ]
        assert _classify(signals) == "MIXED"

    def test_risk_off_two_signals(self):
        signals = [
            {"name": "a", "direction": "risk_off", "detail": ""},
            {"name": "b", "direction": "risk_off", "detail": ""},
            {"name": "c", "direction": "neutral", "detail": ""},
        ]
        assert _classify(signals) == "RISK-OFF"

    def test_all_neutral_is_mixed(self):
        signals = [
            {"name": "a", "direction": "neutral", "detail": ""},
            {"name": "b", "direction": "neutral", "detail": ""},
        ]
        assert _classify(signals) == "MIXED"

    def test_single_risk_on_is_mixed(self):
        signals = [
            {"name": "a", "direction": "risk_on", "detail": ""},
            {"name": "b", "direction": "neutral", "detail": ""},
        ]
        assert _classify(signals) == "MIXED"


# ---------------------------------------------------------------------------
# _build_reason
# ---------------------------------------------------------------------------


class TestBuildReason:
    def test_joins_non_neutral(self):
        signals = [
            {"name": "a", "direction": "risk_on", "detail": "alpha"},
            {"name": "b", "direction": "neutral", "detail": "beta"},
            {"name": "c", "direction": "risk_off", "detail": "gamma"},
        ]
        assert _build_reason(signals) == "alpha; gamma"

    def test_all_neutral(self):
        signals = [
            {"name": "a", "direction": "neutral", "detail": "x"},
        ]
        assert "Insufficient data" in _build_reason(signals)


# ---------------------------------------------------------------------------
# classify_regime (full integration)
# ---------------------------------------------------------------------------


class TestClassifyRegime:
    @pytest.mark.asyncio
    async def test_clear_risk_on(self):
        """SPX above MA, VIXY falling, HY tight → RISK-ON."""
        await _seed_spx_history(5000.0, 20)
        await _insert_snapshot("SPY", 5200.0, change_pct=0.5)
        await _insert_snapshot("VIXY", 22.0, change_pct=-7.0)
        await _insert_snapshot("BAMLH0A0HYM2", 3.2)
        await _insert_snapshot("UUP", 26.8, change_pct=0.2)
        await _insert_snapshot("GLD", 2050.0, change_pct=0.3)

        session = await get_session()
        try:
            result = await classify_regime(session)
        finally:
            await session.close()

        assert result["label"] == "RISK-ON"
        assert "S&P above" in result["reason"]
        assert result["timestamp"]
        assert len(result["signals"]) == 5

    @pytest.mark.asyncio
    async def test_clear_risk_off(self):
        """SPX below MA, VIXY spiking, HY widening → RISK-OFF."""
        await _seed_spx_history(5000.0, 20)

        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        await _insert_snapshot("BAMLH0A0HYM2", 3.50, timestamp=week_ago)

        await _insert_snapshot("SPY", 4800.0, change_pct=-2.0)
        await _insert_snapshot("VIXY", 32.0, change_pct=8.0)
        await _insert_snapshot("BAMLH0A0HYM2", 3.65)
        await _insert_snapshot("UUP", 28.0, change_pct=1.5)
        await _insert_snapshot("GLD", 2100.0, change_pct=2.0)

        session = await get_session()
        try:
            result = await classify_regime(session)
        finally:
            await session.close()

        assert result["label"] == "RISK-OFF"
        assert "below" in result["reason"]

    @pytest.mark.asyncio
    async def test_mixed_conflicting_signals(self):
        """SPX above MA but VIXY spiking → MIXED."""
        await _seed_spx_history(5000.0, 20)

        await _insert_snapshot("SPY", 5200.0, change_pct=0.5)
        await _insert_snapshot("VIXY", 30.0, change_pct=7.0)
        await _insert_snapshot("BAMLH0A0HYM2", 4.0)
        await _insert_snapshot("UUP", 26.8, change_pct=0.2)
        await _insert_snapshot("GLD", 2050.0, change_pct=0.3)

        session = await get_session()
        try:
            result = await classify_regime(session)
        finally:
            await session.close()

        assert result["label"] == "MIXED"

    @pytest.mark.asyncio
    async def test_empty_db_mixed(self):
        """No data at all → MIXED with insufficient-data message."""
        session = await get_session()
        try:
            result = await classify_regime(session)
        finally:
            await session.close()

        assert result["label"] == "MIXED"
        assert "Insufficient data" in result["reason"]

    @pytest.mark.asyncio
    async def test_partial_data(self):
        """Only VIXY available, everything else missing → MIXED."""
        await _insert_snapshot("VIXY", 22.0, change_pct=-7.0)

        session = await get_session()
        try:
            result = await classify_regime(session)
        finally:
            await session.close()

        assert result["label"] == "MIXED"


# ---------------------------------------------------------------------------
# Config consistency
# ---------------------------------------------------------------------------


class TestRegimeConfig:
    def test_required_thresholds_exist(self):
        from backend.config import REGIME_THRESHOLDS

        required = [
            "vixy_spike_pct",
            "vixy_drop_pct",
            "hy_spread_risk_on",
            "hy_spread_risk_off",
            "hy_spread_widening_bps",
            "uup_spike_pct",
            "spx_ma_period",
            "gold_safe_haven_pct",
        ]
        for key in required:
            assert key in REGIME_THRESHOLDS, f"Missing threshold: {key}"
