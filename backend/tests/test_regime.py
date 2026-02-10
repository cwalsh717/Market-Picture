"""Tests for the rule-based regime classification module.

Covers:
- Each signal evaluator in isolation (SPX trend, VIX, HY spread, DXY, gold)
- Aggregation logic (_classify)
- Reason builder
- Full classify_regime integration with various market scenarios
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiosqlite
import pytest

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
def _use_temp_db(tmp_path, monkeypatch):
    """Point the database at a temporary file for every test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("backend.config.DATABASE_PATH", db_path)
    monkeypatch.setattr("backend.db.DATABASE_PATH", db_path)
    asyncio.get_event_loop().run_until_complete(_init_temp_db(db_path))


async def _init_temp_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                asset_class TEXT    NOT NULL,
                price       REAL    NOT NULL,
                change_pct  REAL,
                change_abs  REAL,
                timestamp   TEXT    NOT NULL
            );
            """
        )
        await conn.commit()


async def _insert_snapshot(
    db_path: str,
    symbol: str,
    price: float,
    change_pct: float | None = 0.0,
    timestamp: str | None = None,
) -> None:
    """Insert one row into market_snapshots."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO market_snapshots
                (symbol, asset_class, price, change_pct, change_abs, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (symbol, "test", price, change_pct, 0.0, timestamp),
        )
        await conn.commit()


async def _conn(db_path: str) -> aiosqlite.Connection:
    c = await aiosqlite.connect(db_path)
    c.row_factory = aiosqlite.Row
    return c


async def _seed_spx_history(db_path: str, base_price: float, days: int) -> None:
    """Insert one SPX snapshot per day for *days* trading days."""
    now = datetime.now(timezone.utc)
    for i in range(days):
        ts = (now - timedelta(days=i + 1)).isoformat()
        await _insert_snapshot(db_path, "SPX", base_price, timestamp=ts)


# ---------------------------------------------------------------------------
# _eval_spx_trend
# ---------------------------------------------------------------------------


class TestSpxTrend:
    @pytest.mark.asyncio
    async def test_above_ma(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _seed_spx_history(db_path, 5000.0, 20)
        await _insert_snapshot(db_path, "SPX", 5200.0)

        conn = await _conn(db_path)
        try:
            sig = await _eval_spx_trend(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_on"
        assert "above" in sig["detail"]

    @pytest.mark.asyncio
    async def test_below_ma(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _seed_spx_history(db_path, 5000.0, 20)
        await _insert_snapshot(db_path, "SPX", 4800.0)

        conn = await _conn(db_path)
        try:
            sig = await _eval_spx_trend(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_off"
        assert "below" in sig["detail"]

    @pytest.mark.asyncio
    async def test_insufficient_history(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _seed_spx_history(db_path, 5000.0, 10)
        await _insert_snapshot(db_path, "SPX", 5100.0)

        conn = await _conn(db_path)
        try:
            sig = await _eval_spx_trend(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            sig = await _eval_spx_trend(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"


# ---------------------------------------------------------------------------
# _eval_vix
# ---------------------------------------------------------------------------


class TestVix:
    @pytest.mark.asyncio
    async def test_low_vix_risk_on(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "VIX", 18.0)

        conn = await _conn(db_path)
        try:
            sig = await _eval_vix(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_on"
        assert "18.0" in sig["detail"]

    @pytest.mark.asyncio
    async def test_high_vix_risk_off(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "VIX", 28.0)

        conn = await _conn(db_path)
        try:
            sig = await _eval_vix(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_off"
        assert "28.0" in sig["detail"]

    @pytest.mark.asyncio
    async def test_neutral_range(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "VIX", 22.0)

        conn = await _conn(db_path)
        try:
            sig = await _eval_vix(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            sig = await _eval_vix(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"


# ---------------------------------------------------------------------------
# _eval_hy_spread
# ---------------------------------------------------------------------------


class TestHySpread:
    @pytest.mark.asyncio
    async def test_elevated_level_risk_off(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 5.5)

        conn = await _conn(db_path)
        try:
            sig = await _eval_hy_spread(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_off"
        assert "elevated" in sig["detail"]

    @pytest.mark.asyncio
    async def test_widening_wow_risk_off(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 3.50, timestamp=week_ago)
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 3.65)  # +15 bps

        conn = await _conn(db_path)
        try:
            sig = await _eval_hy_spread(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_off"
        assert "widening" in sig["detail"]

    @pytest.mark.asyncio
    async def test_tight_and_stable_risk_on(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 3.30, timestamp=week_ago)
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 3.25)  # tightening

        conn = await _conn(db_path)
        try:
            sig = await _eval_hy_spread(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_on"
        assert "tight" in sig["detail"]

    @pytest.mark.asyncio
    async def test_no_history_falls_back_to_level(self, tmp_path):
        """No WoW data but low spread → still risk-on."""
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 3.2)

        conn = await _conn(db_path)
        try:
            sig = await _eval_hy_spread(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_on"

    @pytest.mark.asyncio
    async def test_neutral_zone(self, tmp_path):
        """Spread in neutral range, stable WoW."""
        db_path = str(tmp_path / "test.db")
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 4.0, timestamp=week_ago)
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 4.05)  # +5 bps, below threshold

        conn = await _conn(db_path)
        try:
            sig = await _eval_hy_spread(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            sig = await _eval_hy_spread(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"


# ---------------------------------------------------------------------------
# _eval_dxy
# ---------------------------------------------------------------------------


class TestDxy:
    @pytest.mark.asyncio
    async def test_spiking_risk_off(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "DXY", 105.0, change_pct=1.5)

        conn = await _conn(db_path)
        try:
            sig = await _eval_dxy(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_off"
        assert "spiking" in sig["detail"]

    @pytest.mark.asyncio
    async def test_stable_neutral(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "DXY", 103.0, change_pct=0.2)

        conn = await _conn(db_path)
        try:
            sig = await _eval_dxy(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            sig = await _eval_dxy(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"


# ---------------------------------------------------------------------------
# _eval_gold_vs_equities
# ---------------------------------------------------------------------------


class TestGoldVsEquities:
    @pytest.mark.asyncio
    async def test_gold_outperforming_risk_off(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "XAU", 2100.0, change_pct=2.0)
        await _insert_snapshot(db_path, "SPX", 5000.0, change_pct=0.5)

        conn = await _conn(db_path)
        try:
            sig = await _eval_gold_vs_equities(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "risk_off"
        assert "outperforming" in sig["detail"]

    @pytest.mark.asyncio
    async def test_gold_up_but_below_threshold(self, tmp_path):
        """Gold beating S&P but not up enough to trigger safe-haven."""
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "XAU", 2100.0, change_pct=0.5)
        await _insert_snapshot(db_path, "SPX", 5000.0, change_pct=0.1)

        conn = await _conn(db_path)
        try:
            sig = await _eval_gold_vs_equities(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_spx_outperforming_neutral(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "XAU", 2100.0, change_pct=0.5)
        await _insert_snapshot(db_path, "SPX", 5000.0, change_pct=1.5)

        conn = await _conn(db_path)
        try:
            sig = await _eval_gold_vs_equities(conn)
        finally:
            await conn.close()

        assert sig["direction"] == "neutral"

    @pytest.mark.asyncio
    async def test_no_data(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            sig = await _eval_gold_vs_equities(conn)
        finally:
            await conn.close()

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
    async def test_clear_risk_on(self, tmp_path):
        """SPX above MA, VIX low, HY tight → RISK-ON."""
        db_path = str(tmp_path / "test.db")
        await _seed_spx_history(db_path, 5000.0, 20)
        await _insert_snapshot(db_path, "SPX", 5200.0, change_pct=0.5)
        await _insert_snapshot(db_path, "VIX", 16.0)
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 3.2)
        await _insert_snapshot(db_path, "DXY", 103.0, change_pct=0.2)
        await _insert_snapshot(db_path, "XAU", 2050.0, change_pct=0.3)

        conn = await _conn(db_path)
        try:
            result = await classify_regime(conn)
        finally:
            await conn.close()

        assert result["label"] == "RISK-ON"
        assert "S&P above" in result["reason"]
        assert result["timestamp"]
        assert len(result["signals"]) == 5

    @pytest.mark.asyncio
    async def test_clear_risk_off(self, tmp_path):
        """SPX below MA, VIX elevated, HY widening → RISK-OFF."""
        db_path = str(tmp_path / "test.db")
        await _seed_spx_history(db_path, 5000.0, 20)

        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 3.50, timestamp=week_ago)

        await _insert_snapshot(db_path, "SPX", 4800.0, change_pct=-2.0)
        await _insert_snapshot(db_path, "VIX", 30.0)
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 3.65)
        await _insert_snapshot(db_path, "DXY", 106.0, change_pct=1.5)
        await _insert_snapshot(db_path, "XAU", 2100.0, change_pct=2.0)

        conn = await _conn(db_path)
        try:
            result = await classify_regime(conn)
        finally:
            await conn.close()

        assert result["label"] == "RISK-OFF"
        assert "below" in result["reason"]

    @pytest.mark.asyncio
    async def test_mixed_conflicting_signals(self, tmp_path):
        """SPX above MA but VIX elevated → MIXED."""
        db_path = str(tmp_path / "test.db")
        await _seed_spx_history(db_path, 5000.0, 20)

        await _insert_snapshot(db_path, "SPX", 5200.0, change_pct=0.5)
        await _insert_snapshot(db_path, "VIX", 28.0)
        await _insert_snapshot(db_path, "BAMLH0A0HYM2", 4.0)
        await _insert_snapshot(db_path, "DXY", 103.0, change_pct=0.2)
        await _insert_snapshot(db_path, "XAU", 2050.0, change_pct=0.3)

        conn = await _conn(db_path)
        try:
            result = await classify_regime(conn)
        finally:
            await conn.close()

        assert result["label"] == "MIXED"

    @pytest.mark.asyncio
    async def test_empty_db_mixed(self, tmp_path):
        """No data at all → MIXED with insufficient-data message."""
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            result = await classify_regime(conn)
        finally:
            await conn.close()

        assert result["label"] == "MIXED"
        assert "Insufficient data" in result["reason"]

    @pytest.mark.asyncio
    async def test_partial_data(self, tmp_path):
        """Only VIX available, everything else missing → MIXED."""
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "VIX", 18.0)

        conn = await _conn(db_path)
        try:
            result = await classify_regime(conn)
        finally:
            await conn.close()

        assert result["label"] == "MIXED"


# ---------------------------------------------------------------------------
# Config consistency
# ---------------------------------------------------------------------------


class TestRegimeConfig:
    def test_required_thresholds_exist(self):
        from backend.config import REGIME_THRESHOLDS

        required = [
            "vix_risk_on",
            "vix_risk_off",
            "hy_spread_risk_on",
            "hy_spread_risk_off",
            "hy_spread_widening_bps",
            "dxy_spike_pct",
            "spx_ma_period",
            "gold_safe_haven_pct",
        ]
        for key in required:
            assert key in REGIME_THRESHOLDS, f"Missing threshold: {key}"
