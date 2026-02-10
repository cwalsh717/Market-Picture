"""Tests for the cross-asset correlation detection module.

Covers:
- Pearson correlation (pure function)
- Daily returns computation
- Co-movement grouping (1D)
- Anomaly detectors (unexpected convergence, broken correlation, scarcity)
- 1D anomaly detection
- DB helpers (daily close series, latest snapshots)
- Full detect_correlations integration
- Config consistency
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from backend.intelligence.correlations import (
    _align_returns,
    _compute_correlation_matrix,
    _compute_daily_returns,
    _detect_1d_anomalies,
    _detect_broken_correlations,
    _detect_scarcity_divergence,
    _detect_unexpected_convergence,
    _get_all_latest_snapshots,
    _get_daily_close_series,
    _group_by_comovement,
    _group_by_correlation,
    _normalize_pair,
    _pearson_r,
    detect_correlations,
)

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


async def _seed_daily_history(
    db_path: str,
    symbol: str,
    prices: list[float],
    start_days_ago: int | None = None,
) -> None:
    """Insert one snapshot per day for *symbol* with given price sequence.

    Prices are inserted oldest-first.  If *start_days_ago* is ``None``,
    it defaults to ``len(prices)``.
    """
    if start_days_ago is None:
        start_days_ago = len(prices)
    now = datetime.now(timezone.utc)
    for i, price in enumerate(prices):
        days_back = start_days_ago - i
        ts = (now - timedelta(days=days_back)).isoformat()
        await _insert_snapshot(db_path, symbol, price, timestamp=ts)


# ---------------------------------------------------------------------------
# _pearson_r
# ---------------------------------------------------------------------------


class TestPearsonR:
    def test_perfect_positive(self):
        r = _pearson_r([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
        assert r is not None
        assert abs(r - 1.0) < 1e-9

    def test_perfect_negative(self):
        r = _pearson_r([1, 2, 3, 4, 5], [10, 8, 6, 4, 2])
        assert r is not None
        assert abs(r - (-1.0)) < 1e-9

    def test_uncorrelated(self):
        r = _pearson_r([1, 2, 3, 4, 5], [3, 1, 4, 1, 5])
        assert r is not None
        assert abs(r) < 0.5

    def test_insufficient_data(self, monkeypatch):
        monkeypatch.setitem(
            __import__("backend.config", fromlist=["CORRELATION_CONFIG"]).CORRELATION_CONFIG,
            "min_data_points",
            5,
        )
        r = _pearson_r([1, 2, 3], [4, 5, 6])
        assert r is None

    def test_zero_variance(self):
        r = _pearson_r([5, 5, 5, 5, 5], [1, 2, 3, 4, 5])
        assert r is None

    def test_different_lengths_uses_shorter(self):
        r = _pearson_r([1, 2, 3, 4, 5, 6, 7], [2, 4, 6, 8, 10])
        assert r is not None
        assert abs(r - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# _compute_daily_returns
# ---------------------------------------------------------------------------


class TestComputeDailyReturns:
    def test_simple_returns(self):
        series = [("d1", 100.0), ("d2", 110.0), ("d3", 105.0)]
        returns = _compute_daily_returns(series)
        assert len(returns) == 2
        assert abs(returns[0][1] - 10.0) < 1e-9
        assert abs(returns[1][1] - (-50.0 / 11)) < 1e-6

    def test_single_price(self):
        returns = _compute_daily_returns([("d1", 100.0)])
        assert returns == []

    def test_zero_price_skipped(self):
        series = [("d1", 0.0), ("d2", 100.0), ("d3", 110.0)]
        returns = _compute_daily_returns(series)
        # First return skipped (prev=0), second computed.
        assert len(returns) == 1
        assert abs(returns[0][1] - 10.0) < 1e-9


# ---------------------------------------------------------------------------
# _align_returns
# ---------------------------------------------------------------------------


class TestAlignReturns:
    def test_shared_dates_only(self):
        a = [("d1", 1.0), ("d2", 2.0), ("d3", 3.0)]
        b = [("d2", 20.0), ("d3", 30.0), ("d4", 40.0)]
        xs, ys = _align_returns(a, b)
        assert xs == [2.0, 3.0]
        assert ys == [20.0, 30.0]

    def test_no_overlap(self):
        a = [("d1", 1.0)]
        b = [("d2", 2.0)]
        xs, ys = _align_returns(a, b)
        assert xs == []
        assert ys == []


# ---------------------------------------------------------------------------
# _normalize_pair
# ---------------------------------------------------------------------------


class TestNormalizePair:
    def test_already_sorted(self):
        assert _normalize_pair("A", "B") == ("A", "B")

    def test_reversed(self):
        assert _normalize_pair("SPX", "BTC/USD") == ("BTC/USD", "SPX")


# ---------------------------------------------------------------------------
# _group_by_comovement
# ---------------------------------------------------------------------------


class TestGroupByComovement:
    def test_groups_by_direction(self):
        snapshots = {
            "SPX": {"change_pct": 2.0},
            "NDX": {"change_pct": 2.5},
            "RUT": {"change_pct": -1.5},
            "VIX": {"change_pct": -2.0},
        }
        groups = _group_by_comovement(snapshots)
        up = [g for g in groups if g["direction"] == "up"]
        down = [g for g in groups if g["direction"] == "down"]
        assert len(up) == 1
        assert set(up[0]["symbols"]) == {"SPX", "NDX"}
        assert len(down) == 1
        assert set(down[0]["symbols"]) == {"RUT", "VIX"}

    def test_filters_flat_assets(self):
        snapshots = {
            "SPX": {"change_pct": 0.1},  # below min threshold
            "NDX": {"change_pct": 0.05},
        }
        groups = _group_by_comovement(snapshots)
        assert groups == []

    def test_magnitude_banding(self):
        """Assets far apart in magnitude get split into separate groups."""
        snapshots = {
            "SPX": {"change_pct": 5.0},
            "NDX": {"change_pct": 4.5},
            "RUT": {"change_pct": 1.0},
            "UKX": {"change_pct": 0.8},
        }
        groups = _group_by_comovement(snapshots)
        up_groups = [g for g in groups if g["direction"] == "up"]
        # SPX/NDX (~5%) and RUT/UKX (~1%) should be separate bands.
        assert len(up_groups) == 2

    def test_empty_snapshots(self):
        assert _group_by_comovement({}) == []

    def test_none_change_pct_skipped(self):
        snapshots = {"SPX": {"change_pct": None}}
        assert _group_by_comovement(snapshots) == []

    def test_single_asset_direction_not_grouped(self):
        """A lone asset in a direction doesn't form a group."""
        snapshots = {
            "SPX": {"change_pct": 2.0},
            "VIX": {"change_pct": -1.5},
        }
        groups = _group_by_comovement(snapshots)
        assert groups == []


# ---------------------------------------------------------------------------
# _detect_unexpected_convergence
# ---------------------------------------------------------------------------


class TestDetectUnexpectedConvergence:
    def test_btc_spx_convergence_flagged(self):
        pairs = {
            ("BTC/USD", "SPX"): {
                "symbol_a": "BTC/USD",
                "symbol_b": "SPX",
                "correlation": 0.75,
                "data_points": 20,
            },
        }
        anomalies = _detect_unexpected_convergence(pairs)
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "unexpected_convergence"
        assert "unusually correlated" in anomalies[0]["detail"]

    def test_normal_uncorrelated_no_flag(self):
        pairs = {
            ("BTC/USD", "SPX"): {
                "symbol_a": "BTC/USD",
                "symbol_b": "SPX",
                "correlation": 0.15,
                "data_points": 20,
            },
        }
        anomalies = _detect_unexpected_convergence(pairs)
        assert anomalies == []

    def test_pair_not_in_baseline_skipped(self):
        pairs = {
            ("AAPL", "TSLA"): {
                "symbol_a": "AAPL",
                "symbol_b": "TSLA",
                "correlation": 0.90,
                "data_points": 20,
            },
        }
        anomalies = _detect_unexpected_convergence(pairs)
        assert anomalies == []


# ---------------------------------------------------------------------------
# _detect_broken_correlations
# ---------------------------------------------------------------------------


class TestDetectBrokenCorrelations:
    def test_spx_vix_breakdown_flagged(self):
        pairs = {
            ("SPX", "VIX"): {
                "symbol_a": "SPX",
                "symbol_b": "VIX",
                "correlation": 0.10,
                "data_points": 20,
            },
        }
        anomalies = _detect_broken_correlations(pairs)
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "broken_correlation"

    def test_spx_ndx_diverging_flagged(self):
        pairs = {
            ("NDX", "SPX"): {
                "symbol_a": "NDX",
                "symbol_b": "SPX",
                "correlation": 0.30,
                "data_points": 20,
            },
        }
        anomalies = _detect_broken_correlations(pairs)
        assert len(anomalies) == 1

    def test_normal_high_correlation_no_flag(self):
        pairs = {
            ("NDX", "SPX"): {
                "symbol_a": "NDX",
                "symbol_b": "SPX",
                "correlation": 0.88,
                "data_points": 20,
            },
        }
        anomalies = _detect_broken_correlations(pairs)
        assert anomalies == []


# ---------------------------------------------------------------------------
# _detect_scarcity_divergence
# ---------------------------------------------------------------------------


class TestDetectScarcityDivergence:
    def test_ura_diverging_flagged(self):
        pairs = {
            ("SPX", "URA"): {
                "symbol_a": "SPX",
                "symbol_b": "URA",
                "correlation": -0.20,
                "data_points": 20,
            },
        }
        anomalies = _detect_scarcity_divergence(pairs)
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "scarcity_divergence"
        assert "Uranium" in anomalies[0]["detail"]

    def test_ura_tracking_normally_no_flag(self):
        pairs = {
            ("SPX", "URA"): {
                "symbol_a": "SPX",
                "symbol_b": "URA",
                "correlation": 0.35,
                "data_points": 20,
            },
        }
        anomalies = _detect_scarcity_divergence(pairs)
        assert anomalies == []

    def test_missing_pair_skipped(self):
        anomalies = _detect_scarcity_divergence({})
        assert anomalies == []


# ---------------------------------------------------------------------------
# _detect_1d_anomalies
# ---------------------------------------------------------------------------


class TestDetect1dAnomalies:
    def test_vix_up_with_spx_up(self):
        """SPX and VIX both up is anomalous (normally inversely correlated)."""
        snapshots = {
            "SPX": {"change_pct": 2.0},
            "VIX": {"change_pct": 1.5},
        }
        anomalies = _detect_1d_anomalies(snapshots)
        types = [a["anomaly_type"] for a in anomalies]
        assert "broken_correlation" in types

    def test_btc_lockstep_with_spx(self):
        """BTC and SPX both up is anomalous (normally uncorrelated)."""
        snapshots = {
            "BTC/USD": {"change_pct": 3.0},
            "SPX": {"change_pct": 2.5},
        }
        anomalies = _detect_1d_anomalies(snapshots)
        types = [a["anomaly_type"] for a in anomalies]
        assert "unexpected_convergence" in types

    def test_normally_correlated_opposite_directions(self):
        """NDX and SPX moving opposite is anomalous (normally r=0.90)."""
        snapshots = {
            "NDX": {"change_pct": -2.0},
            "SPX": {"change_pct": 1.5},
        }
        anomalies = _detect_1d_anomalies(snapshots)
        types = [a["anomaly_type"] for a in anomalies]
        assert "broken_correlation" in types

    def test_flat_assets_skipped(self):
        snapshots = {
            "SPX": {"change_pct": 0.1},
            "VIX": {"change_pct": 0.1},
        }
        anomalies = _detect_1d_anomalies(snapshots)
        assert anomalies == []

    def test_missing_data_graceful(self):
        snapshots = {"SPX": {"change_pct": 2.0}}
        anomalies = _detect_1d_anomalies(snapshots)
        assert anomalies == []


# ---------------------------------------------------------------------------
# _get_all_latest_snapshots (async, DB)
# ---------------------------------------------------------------------------


class TestGetAllLatestSnapshots:
    @pytest.mark.asyncio
    async def test_returns_latest_per_symbol(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        new_ts = datetime.now(timezone.utc).isoformat()

        await _insert_snapshot(db_path, "SPX", 5000.0, timestamp=old_ts)
        await _insert_snapshot(db_path, "SPX", 5100.0, timestamp=new_ts)
        await _insert_snapshot(db_path, "NDX", 18000.0, timestamp=new_ts)

        conn = await _conn(db_path)
        try:
            result = await _get_all_latest_snapshots(conn)
        finally:
            await conn.close()

        assert result["SPX"]["price"] == 5100.0
        assert result["NDX"]["price"] == 18000.0

    @pytest.mark.asyncio
    async def test_empty_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            result = await _get_all_latest_snapshots(conn)
        finally:
            await conn.close()
        assert result == {}


# ---------------------------------------------------------------------------
# _get_daily_close_series (async, DB)
# ---------------------------------------------------------------------------


class TestGetDailyCloseSeries:
    @pytest.mark.asyncio
    async def test_one_close_per_day(self, tmp_path):
        db_path = str(tmp_path / "test.db")

        # Two snapshots on the same calendar day -- should keep latest.
        day_ts_early = "2026-02-09T10:00:00+00:00"
        day_ts_late = "2026-02-09T15:00:00+00:00"
        await _insert_snapshot(db_path, "SPX", 5000.0, timestamp=day_ts_early)
        await _insert_snapshot(db_path, "SPX", 5100.0, timestamp=day_ts_late)

        conn = await _conn(db_path)
        try:
            result = await _get_daily_close_series(conn, ["SPX"], days_back=3)
        finally:
            await conn.close()

        assert "SPX" in result
        assert len(result["SPX"]) == 1
        assert result["SPX"][0][1] == 5100.0

    @pytest.mark.asyncio
    async def test_respects_cutoff(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=30)).isoformat()
        new_ts = (now - timedelta(days=1)).isoformat()

        await _insert_snapshot(db_path, "SPX", 5000.0, timestamp=old_ts)
        await _insert_snapshot(db_path, "SPX", 5100.0, timestamp=new_ts)

        conn = await _conn(db_path)
        try:
            result = await _get_daily_close_series(conn, ["SPX"], days_back=7)
        finally:
            await conn.close()

        assert len(result["SPX"]) == 1  # Only recent one within 7 days.

    @pytest.mark.asyncio
    async def test_missing_symbols(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            result = await _get_daily_close_series(conn, ["NOPE"], days_back=7)
        finally:
            await conn.close()
        assert result == {}


# ---------------------------------------------------------------------------
# _group_by_correlation
# ---------------------------------------------------------------------------


class TestGroupByCorrelation:
    def test_forms_groups_above_threshold(self):
        pairs = {
            ("NDX", "SPX"): {
                "symbol_a": "NDX",
                "symbol_b": "SPX",
                "correlation": 0.92,
                "data_points": 20,
            },
        }
        returns = {
            "NDX": [("d1", 1.0), ("d2", 2.0)],
            "SPX": [("d1", 1.5), ("d2", 1.8)],
        }
        groups = _group_by_correlation(pairs, returns, threshold=0.7)
        assert len(groups) == 1
        assert set(groups[0]["symbols"]) == {"NDX", "SPX"}

    def test_below_threshold_no_group(self):
        pairs = {
            ("NDX", "SPX"): {
                "symbol_a": "NDX",
                "symbol_b": "SPX",
                "correlation": 0.50,
                "data_points": 20,
            },
        }
        returns = {"NDX": [("d1", 1.0)], "SPX": [("d1", 1.5)]}
        groups = _group_by_correlation(pairs, returns, threshold=0.7)
        assert groups == []


# ---------------------------------------------------------------------------
# detect_correlations (full integration)
# ---------------------------------------------------------------------------


class TestDetectCorrelations:
    @pytest.mark.asyncio
    async def test_1d_with_seeded_data(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        await _insert_snapshot(db_path, "SPX", 5100.0, change_pct=2.0)
        await _insert_snapshot(db_path, "NDX", 18000.0, change_pct=2.5)
        await _insert_snapshot(db_path, "VIX", 20.0, change_pct=1.5)

        conn = await _conn(db_path)
        try:
            result = await detect_correlations(conn, period="1D")
        finally:
            await conn.close()

        assert result["period"] == "1D"
        assert result["data_points"] == 0
        assert isinstance(result["groups"], list)
        assert isinstance(result["anomalies"], list)
        assert result["timestamp"]
        # SPX and VIX both up should produce an anomaly.
        types = [a["anomaly_type"] for a in result["anomalies"]]
        assert "broken_correlation" in types

    @pytest.mark.asyncio
    async def test_1m_with_sufficient_history(self, tmp_path):
        """Seed 20+ days of correlated prices and verify detection."""
        db_path = str(tmp_path / "test.db")

        # SPX and NDX move together (perfectly correlated prices).
        spx_prices = [5000 + i * 10 for i in range(25)]
        ndx_prices = [18000 + i * 30 for i in range(25)]
        await _seed_daily_history(db_path, "SPX", spx_prices)
        await _seed_daily_history(db_path, "NDX", ndx_prices)

        conn = await _conn(db_path)
        try:
            result = await detect_correlations(conn, period="1M")
        finally:
            await conn.close()

        assert result["period"] == "1M"
        assert result["data_points"] > 0
        # Should find at least one notable pair.
        assert len(result["notable_pairs"]) >= 1

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            result = await detect_correlations(conn, period="1M")
        finally:
            await conn.close()

        assert result["groups"] == []
        assert result["anomalies"] == []
        assert result["notable_pairs"] == []
        assert result["data_points"] == 0

    @pytest.mark.asyncio
    async def test_insufficient_data_for_correlation(self, tmp_path):
        """Only 2 days of data -- below min_data_points threshold."""
        db_path = str(tmp_path / "test.db")
        await _seed_daily_history(db_path, "SPX", [5000, 5100])
        await _seed_daily_history(db_path, "NDX", [18000, 18100])

        conn = await _conn(db_path)
        try:
            result = await detect_correlations(conn, period="1W")
        finally:
            await conn.close()

        assert result["data_points"] == 0
        assert result["groups"] == []

    @pytest.mark.asyncio
    async def test_output_structure(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = await _conn(db_path)
        try:
            result = await detect_correlations(conn, period="1D")
        finally:
            await conn.close()

        required_keys = {"period", "timestamp", "data_points", "groups",
                         "anomalies", "notable_pairs"}
        assert required_keys == set(result.keys())


# ---------------------------------------------------------------------------
# Config consistency
# ---------------------------------------------------------------------------


class TestCorrelationConfig:
    def test_required_config_keys_exist(self):
        from backend.config import CORRELATION_CONFIG

        required = [
            "min_data_points",
            "anomaly_deviation_threshold",
            "comovement_magnitude_band",
            "comovement_min_change_pct",
        ]
        for key in required:
            assert key in CORRELATION_CONFIG, f"Missing config key: {key}"

    def test_baseline_keys_sorted(self):
        from backend.config import BASELINE_CORRELATIONS

        for a, b in BASELINE_CORRELATIONS:
            assert a <= b, f"Baseline key ({a}, {b}) not sorted alphabetically"

    def test_baseline_symbols_exist(self):
        from backend.config import ASSETS, BASELINE_CORRELATIONS, FRED_SERIES

        all_symbols: set[str] = set()
        for _cat, syms in ASSETS.items():
            all_symbols.update(syms.keys())
        all_symbols.update(FRED_SERIES.keys())
        all_symbols.add("SPREAD_2S10S")

        for a, b in BASELINE_CORRELATIONS:
            assert a in all_symbols, f"Baseline symbol {a} not in known assets"
            assert b in all_symbols, f"Baseline symbol {b} not in known assets"

    def test_correlation_threshold_exists(self):
        from backend.config import REGIME_THRESHOLDS

        assert "correlation_threshold" in REGIME_THRESHOLDS

    def test_thresholds_reasonable(self):
        from backend.config import CORRELATION_CONFIG

        assert 0 < CORRELATION_CONFIG["anomaly_deviation_threshold"] < 1
        assert CORRELATION_CONFIG["min_data_points"] >= 3
        assert CORRELATION_CONFIG["comovement_min_change_pct"] > 0
