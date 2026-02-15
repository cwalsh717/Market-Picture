"""Tests: verify all data lands in DB correctly.

Covers:
- save_quotes inserts rows with correct fields
- fetch_twelve_data_quotes filters by market hours and persists
- fetch_fred_quotes persists all FRED series
- is_market_open handles normal, overnight, and 24/7 sessions
- get_active_symbols returns correct subsets per time of day
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from backend.config import SYMBOL_ASSET_CLASS, SYMBOL_MARKET_MAP
from backend.db import _migrate_summaries_table, close_db, get_session, init_db
from backend.jobs.daily_update import (
    fetch_fred_quotes,
    fetch_twelve_data_quotes,
    generate_close_summary,
    generate_premarket_summary,
    get_active_symbols,
    is_market_open,
    save_quotes,
)

_ET = ZoneInfo("US/Eastern")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    """Point the database at a temporary SQLite file for every test."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    asyncio.get_event_loop().run_until_complete(init_db(db_url))
    yield
    asyncio.get_event_loop().run_until_complete(close_db())


async def _read_snapshots() -> list[dict]:
    """Read all rows from market_snapshots."""
    session = await get_session()
    try:
        result = await session.execute(
            text("SELECT * FROM market_snapshots ORDER BY id")
        )
        return [dict(r) for r in result.mappings().all()]
    finally:
        await session.close()


async def _read_summaries() -> list[dict]:
    """Read all rows from summaries."""
    session = await get_session()
    try:
        result = await session.execute(
            text("SELECT * FROM summaries ORDER BY id")
        )
        return [dict(r) for r in result.mappings().all()]
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# is_market_open
# ---------------------------------------------------------------------------


class TestIsMarketOpen:
    def test_us_during_hours(self):
        t = datetime(2025, 1, 6, 10, 0, tzinfo=_ET)
        assert is_market_open("US", t) is True

    def test_us_at_open(self):
        t = datetime(2025, 1, 6, 9, 30, tzinfo=_ET)
        assert is_market_open("US", t) is True

    def test_us_at_close(self):
        t = datetime(2025, 1, 6, 16, 0, tzinfo=_ET)
        assert is_market_open("US", t) is True

    def test_us_before_open(self):
        t = datetime(2025, 1, 6, 6, 0, tzinfo=_ET)
        assert is_market_open("US", t) is False

    def test_us_after_close(self):
        t = datetime(2025, 1, 6, 18, 0, tzinfo=_ET)
        assert is_market_open("US", t) is False

    def test_japan_overnight_evening(self):
        """Japan 20:00-02:00 ET: 21:00 should be open."""
        t = datetime(2025, 1, 6, 21, 0, tzinfo=_ET)
        assert is_market_open("Japan", t) is True

    def test_japan_overnight_past_midnight(self):
        """Japan 20:00-02:00 ET: 01:00 should be open."""
        t = datetime(2025, 1, 7, 1, 0, tzinfo=_ET)
        assert is_market_open("Japan", t) is True

    def test_japan_closed_midday(self):
        """Japan 20:00-02:00 ET: 15:00 should be closed."""
        t = datetime(2025, 1, 6, 15, 0, tzinfo=_ET)
        assert is_market_open("Japan", t) is False

    def test_hk_overnight(self):
        """HK 21:30-04:00 ET: 23:00 should be open."""
        t = datetime(2025, 1, 6, 23, 0, tzinfo=_ET)
        assert is_market_open("HK", t) is True

    def test_crypto_always_on(self):
        t = datetime(2025, 1, 6, 3, 0, tzinfo=_ET)
        assert is_market_open("24/7", t) is True

    def test_unknown_market_returns_false(self):
        t = datetime(2025, 1, 6, 12, 0, tzinfo=_ET)
        assert is_market_open("Mars", t) is False

    def test_us_closed_on_saturday(self):
        """Saturday 10 AM ET — US market hours but weekend."""
        t = datetime(2025, 1, 4, 10, 0, tzinfo=_ET)  # Saturday
        assert is_market_open("US", t) is False

    def test_us_closed_on_sunday(self):
        t = datetime(2025, 1, 5, 10, 0, tzinfo=_ET)  # Sunday
        assert is_market_open("US", t) is False

    def test_uk_closed_on_saturday(self):
        t = datetime(2025, 1, 4, 5, 0, tzinfo=_ET)  # Saturday
        assert is_market_open("UK", t) is False

    def test_japan_closed_on_saturday(self):
        t = datetime(2025, 1, 4, 21, 0, tzinfo=_ET)  # Saturday evening
        assert is_market_open("Japan", t) is False

    def test_crypto_open_on_saturday(self):
        t = datetime(2025, 1, 4, 10, 0, tzinfo=_ET)  # Saturday
        assert is_market_open("24/7", t) is True

    def test_crypto_open_on_sunday(self):
        t = datetime(2025, 1, 5, 3, 0, tzinfo=_ET)  # Sunday
        assert is_market_open("24/7", t) is True


# ---------------------------------------------------------------------------
# get_active_symbols
# ---------------------------------------------------------------------------


class TestGetActiveSymbols:
    def test_us_hours_includes_equities_and_crypto(self):
        t = datetime(2025, 1, 6, 10, 0, tzinfo=_ET)
        active = get_active_symbols(t)
        assert "SPY" in active
        assert "QQQ" in active
        assert "UUP" in active
        assert "USO" in active
        assert "URA" in active
        assert "BTC/USD" in active
        assert "ETH/USD" in active

    def test_us_hours_includes_international_etfs(self):
        """10 AM ET: US-listed international ETFs are active during US hours."""
        t = datetime(2025, 1, 6, 10, 0, tzinfo=_ET)
        active = get_active_symbols(t)
        assert "EWJ" in active
        assert "EWH" in active
        assert "FEZ" in active
        # UK (03:00-11:30 ET) is still open at 10 AM
        assert "UKX" in active

    def test_early_morning_uk_only(self):
        """3 AM ET: UK + crypto only. US-listed ETFs are closed."""
        t = datetime(2025, 1, 6, 3, 0, tzinfo=_ET)
        active = get_active_symbols(t)
        assert "UKX" in active
        assert "BTC/USD" in active
        assert "SPY" not in active
        assert "EWJ" not in active
        assert "FEZ" not in active

    def test_late_night_only_crypto(self):
        """22:00 ET: only crypto (international ETFs are US-listed, closed)."""
        t = datetime(2025, 1, 6, 22, 0, tzinfo=_ET)
        active = get_active_symbols(t)
        assert "BTC/USD" in active
        assert "SPY" not in active
        assert "EWJ" not in active
        assert "EWH" not in active

    def test_all_closed_except_crypto(self):
        """17:00 ET: only crypto should be active."""
        t = datetime(2025, 1, 6, 17, 0, tzinfo=_ET)
        active = get_active_symbols(t)
        assert set(active) == {"BTC/USD", "ETH/USD"}

    def test_weekend_only_crypto(self):
        """Saturday 10 AM ET: only crypto despite US market hours."""
        t = datetime(2025, 1, 4, 10, 0, tzinfo=_ET)  # Saturday
        active = get_active_symbols(t)
        assert set(active) == {"BTC/USD", "ETH/USD"}


# ---------------------------------------------------------------------------
# save_quotes
# ---------------------------------------------------------------------------


class TestSaveQuotes:
    @pytest.mark.asyncio
    async def test_saves_single_quote(self):
        quotes = {
            "SPY": {
                "price": 5100.50,
                "change_pct": 0.75,
                "change_abs": 38.0,
                "timestamp": "2025-01-06 16:00:00",
            },
        }
        saved = await save_quotes(quotes)
        assert saved == 1

        rows = await _read_snapshots()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "SPY"
        assert rows[0]["asset_class"] == "equities"
        assert rows[0]["price"] == 5100.50
        assert rows[0]["change_pct"] == 0.75
        assert rows[0]["change_abs"] == 38.0
        assert rows[0]["timestamp"] == "2025-01-06 16:00:00"

    @pytest.mark.asyncio
    async def test_saves_multiple_quotes(self):
        quotes = {
            "SPY": {"price": 5100.0, "change_pct": 0.5, "change_abs": 25.0, "timestamp": "t1"},
            "BTC/USD": {"price": 97000.0, "change_pct": 2.1, "change_abs": 2000.0, "timestamp": "t1"},
            "DGS10": {"price": 4.25, "change_pct": -0.5, "change_abs": -0.02, "timestamp": "t1"},
        }
        saved = await save_quotes(quotes)
        assert saved == 3

        rows = await _read_snapshots()
        symbols = {r["symbol"] for r in rows}
        assert symbols == {"SPY", "BTC/USD", "DGS10"}

        # Verify asset classes
        asset_classes = {r["symbol"]: r["asset_class"] for r in rows}
        assert asset_classes["SPY"] == "equities"
        assert asset_classes["BTC/USD"] == "crypto"
        assert asset_classes["DGS10"] == "rates"

    @pytest.mark.asyncio
    async def test_skips_quotes_without_price(self):
        quotes = {
            "SPY": {"change_pct": 0.5, "timestamp": "t1"},  # no price
            "QQQ": {"price": 18000.0, "change_pct": 0.3, "change_abs": 50.0, "timestamp": "t1"},
        }
        saved = await save_quotes(quotes)
        assert saved == 1

        rows = await _read_snapshots()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "QQQ"

    @pytest.mark.asyncio
    async def test_empty_quotes_returns_zero(self):
        saved = await save_quotes({})
        assert saved == 0

    @pytest.mark.asyncio
    async def test_unknown_symbol_gets_unknown_asset_class(self):
        quotes = {"FAKE": {"price": 1.0, "timestamp": "t1"}}
        saved = await save_quotes(quotes)
        assert saved == 1

        rows = await _read_snapshots()
        assert rows[0]["asset_class"] == "unknown"


# ---------------------------------------------------------------------------
# fetch_twelve_data_quotes (with mock provider)
# ---------------------------------------------------------------------------


class TestFetchTwelveDataQuotes:
    @pytest.mark.asyncio
    async def test_fetches_and_saves_during_us_hours(self, monkeypatch):
        mock_provider = AsyncMock()
        mock_provider.get_quotes_for_symbols.return_value = {
            "SPY": {"price": 5100.0, "change_pct": 0.5, "change_abs": 25.0, "timestamp": "t1"},
            "BTC/USD": {"price": 97000.0, "change_pct": 2.1, "change_abs": 2000.0, "timestamp": "t1"},
        }

        # Fix time to 10 AM ET (US market open)
        fixed_time = datetime(2025, 1, 6, 10, 0, tzinfo=_ET)
        monkeypatch.setattr(
            "backend.jobs.daily_update.datetime",
            type("MockDatetime", (), {"now": staticmethod(lambda tz: fixed_time), "strptime": datetime.strptime})(),
        )

        await fetch_twelve_data_quotes(provider=mock_provider)

        # Verify provider was called with active symbols
        mock_provider.get_quotes_for_symbols.assert_called_once()
        called_symbols = mock_provider.get_quotes_for_symbols.call_args[0][0]
        assert "SPY" in called_symbols
        assert "BTC/USD" in called_symbols

        # Verify data landed in DB
        rows = await _read_snapshots()
        assert len(rows) == 2
        symbols = {r["symbol"] for r in rows}
        assert symbols == {"SPY", "BTC/USD"}

    @pytest.mark.asyncio
    async def test_skips_when_no_markets_open(self, monkeypatch):
        """At 17:00 ET only crypto is open — but if provider returns empty, nothing saved."""
        mock_provider = AsyncMock()
        mock_provider.get_quotes_for_symbols.return_value = {}

        fixed_time = datetime(2025, 1, 6, 17, 0, tzinfo=_ET)
        monkeypatch.setattr(
            "backend.jobs.daily_update.datetime",
            type("MockDatetime", (), {"now": staticmethod(lambda tz: fixed_time), "strptime": datetime.strptime})(),
        )

        await fetch_twelve_data_quotes(provider=mock_provider)

        rows = await _read_snapshots()
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# fetch_fred_quotes (with mock provider)
# ---------------------------------------------------------------------------


class TestFetchFredQuotes:
    @pytest.mark.asyncio
    async def test_fetches_and_saves_all_fred(self):
        mock_provider = AsyncMock()
        mock_provider.get_all_quotes.return_value = {
            "DGS2": {"price": 4.15, "change_pct": 0.1, "change_abs": 0.004, "timestamp": "2025-01-06"},
            "DGS10": {"price": 4.55, "change_pct": -0.2, "change_abs": -0.009, "timestamp": "2025-01-06"},
            "BAMLC0A0CM": {"price": 1.2, "change_pct": 0.0, "change_abs": 0.0, "timestamp": "2025-01-06"},
            "BAMLH0A0HYM2": {"price": 3.8, "change_pct": -0.5, "change_abs": -0.02, "timestamp": "2025-01-06"},
            "SPREAD_2S10S": {"price": 0.4, "change_pct": 1.0, "change_abs": 0.004, "timestamp": "2025-01-06"},
        }

        await fetch_fred_quotes(provider=mock_provider)

        rows = await _read_snapshots()
        assert len(rows) == 5

        symbols = {r["symbol"] for r in rows}
        assert symbols == {"DGS2", "DGS10", "BAMLC0A0CM", "BAMLH0A0HYM2", "SPREAD_2S10S"}

        # All FRED series should have asset_class "rates"
        for row in rows:
            assert row["asset_class"] == "rates", f"{row['symbol']} should be rates"

    @pytest.mark.asyncio
    async def test_handles_empty_fred_response(self):
        mock_provider = AsyncMock()
        mock_provider.get_all_quotes.return_value = {}

        await fetch_fred_quotes(provider=mock_provider)

        rows = await _read_snapshots()
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Config consistency checks
# ---------------------------------------------------------------------------


class TestConfigConsistency:
    def test_all_twelve_data_symbols_have_market_mapping(self):
        """Every symbol in ASSETS should appear in SYMBOL_MARKET_MAP."""
        from backend.config import ASSETS

        for category, symbols in ASSETS.items():
            for symbol in symbols:
                assert symbol in SYMBOL_MARKET_MAP, (
                    f"{symbol} ({category}) missing from SYMBOL_MARKET_MAP"
                )

    def test_all_twelve_data_symbols_have_asset_class(self):
        """Every symbol in ASSETS should appear in SYMBOL_ASSET_CLASS."""
        from backend.config import ASSETS

        for category, symbols in ASSETS.items():
            for symbol in symbols:
                assert symbol in SYMBOL_ASSET_CLASS, (
                    f"{symbol} ({category}) missing from SYMBOL_ASSET_CLASS"
                )
                assert SYMBOL_ASSET_CLASS[symbol] == category

    def test_fred_series_have_asset_class(self):
        """Every FRED series should map to 'rates' in SYMBOL_ASSET_CLASS."""
        from backend.config import FRED_SERIES

        for series_id in FRED_SERIES:
            assert SYMBOL_ASSET_CLASS[series_id] == "rates"
        assert SYMBOL_ASSET_CLASS["SPREAD_2S10S"] == "rates"

    def test_market_map_values_are_valid(self):
        """Every market region in SYMBOL_MARKET_MAP should exist in MARKET_HOURS or be '24/7'."""
        from backend.config import MARKET_HOURS

        for symbol, market in SYMBOL_MARKET_MAP.items():
            assert market == "24/7" or market in MARKET_HOURS, (
                f"{symbol} maps to unknown market '{market}'"
            )


# ---------------------------------------------------------------------------
# Fake intelligence data for summary persistence tests
# ---------------------------------------------------------------------------

_FAKE_REGIME = {
    "label": "RISK-ON",
    "reason": "Broad risk appetite",
    "signals": [
        {"name": "spx_trend", "direction": "risk_on", "detail": "S&P above 20-day MA"},
        {"name": "vix", "direction": "risk_on", "detail": "VIXY falling (-7.0%)"},
    ],
}

_FAKE_SUMMARY_PREMARKET = {
    "period": "premarket",
    "summary_text": "Markets are calm overnight.",
    "regime_label": "RISK-ON",
    "regime_reason": "Broad risk appetite",
    "timestamp": "2025-01-06T12:00:00+00:00",
}

_FAKE_SUMMARY_CLOSE = {
    "period": "close",
    "summary_text": "A strong day across equities.",
    "regime_label": "RISK-ON",
    "regime_reason": "Broad risk appetite",
    "timestamp": "2025-01-06T21:00:00+00:00",
}


# ---------------------------------------------------------------------------
# Summary persistence
# ---------------------------------------------------------------------------


class TestSummaryPersistence:
    """Verify generate_premarket_summary and generate_close_summary persist
    columns correctly."""

    @pytest.mark.asyncio
    async def test_premarket_persists_all_columns(self, monkeypatch):
        fixed_time = datetime(2025, 1, 6, 8, 0, tzinfo=_ET)
        monkeypatch.setattr(
            "backend.jobs.daily_update.datetime",
            type("MockDT", (), {
                "now": staticmethod(lambda tz: fixed_time),
                "strptime": datetime.strptime,
            })(),
        )

        with patch("backend.intelligence.regime.classify_regime", new_callable=AsyncMock) as mock_regime, \
             patch("backend.intelligence.summary.generate_premarket", new_callable=AsyncMock) as mock_gen:
            mock_regime.return_value = _FAKE_REGIME
            mock_gen.return_value = _FAKE_SUMMARY_PREMARKET

            await generate_premarket_summary()

        rows = await _read_summaries()
        assert len(rows) == 1
        row = rows[0]

        assert row["date"] == "2025-01-06"
        assert row["period"] == "premarket"
        assert row["summary_text"] == "Markets are calm overnight."
        assert row["regime_label"] == "RISK-ON"
        assert row["regime_reason"] == "Broad risk appetite"

        # regime_signals_json should contain the signal breakdowns
        signals = json.loads(row["regime_signals_json"])
        assert len(signals) == 2
        assert signals[0]["name"] == "spx_trend"

    @pytest.mark.asyncio
    async def test_close_persists_all_columns(self, monkeypatch):
        fixed_time = datetime(2025, 1, 6, 16, 30, tzinfo=_ET)
        monkeypatch.setattr(
            "backend.jobs.daily_update.datetime",
            type("MockDT", (), {
                "now": staticmethod(lambda tz: fixed_time),
                "strptime": datetime.strptime,
            })(),
        )

        with patch("backend.intelligence.regime.classify_regime", new_callable=AsyncMock) as mock_regime, \
             patch("backend.intelligence.summary.generate_close", new_callable=AsyncMock) as mock_gen:
            mock_regime.return_value = _FAKE_REGIME
            mock_gen.return_value = _FAKE_SUMMARY_CLOSE

            await generate_close_summary()

        rows = await _read_summaries()
        assert len(rows) == 1
        row = rows[0]

        assert row["period"] == "close"
        assert row["summary_text"] == "A strong day across equities."

        signals = json.loads(row["regime_signals_json"])
        assert len(signals) == 2


# ---------------------------------------------------------------------------
# Summaries table migration
# ---------------------------------------------------------------------------


class TestSummariesMigration:
    """Verify _migrate_summaries_table handles missing columns gracefully."""

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self):
        """Running migration on a DB that already has all columns doesn't fail."""
        session = await get_session()
        try:
            await _migrate_summaries_table(session)
            await _migrate_summaries_table(session)  # second call should not fail

            # Verify we can still insert and read
            await session.execute(
                text("""
                    INSERT INTO summaries
                        (date, period, summary_text, regime_label, regime_reason,
                         regime_signals_json)
                    VALUES (:date, :period, :text, :label, :reason, :signals)
                """),
                {
                    "date": "2025-01-06",
                    "period": "premarket",
                    "text": "test",
                    "label": "RISK-ON",
                    "reason": "test",
                    "signals": "[]",
                },
            )
            await session.commit()

            result = await session.execute(
                text("SELECT regime_signals_json FROM summaries")
            )
            row = result.mappings().first()
            assert row["regime_signals_json"] == "[]"
        finally:
            await session.close()
