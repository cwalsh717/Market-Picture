"""On-demand historical data cache service.

Checks daily_history for a symbol; if missing, fetches from Twelve Data
(interval=1day, outputsize=5000), stores in DB, and returns data.
Handles rate limiting and batch operations for startup backfill and
daily incremental updates.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from zoneinfo import ZoneInfo

from sqlalchemy import text

from backend.db import get_dialect, get_session
from backend.providers.twelve_data import TwelveDataProvider

logger = logging.getLogger(__name__)

_ET = ZoneInfo("US/Eastern")

# Rate limiting: 55 credits/min on Twelve Data Grow tier.
# Each time_series call = 1 credit.  Leave headroom for live quote fetches.
_BACKFILL_DELAY_SECONDS = 1.5   # ~40 req/min, leaving 15 credits for live
_DAILY_APPEND_DELAY_SECONDS = 1.2

# Range string -> calendar days to look back (None = special handling)
_RANGE_DAYS: dict[str, int | None] = {
    "1D": 1,
    "5D": 5,
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
    "YTD": None,
    "5Y": 1825,
    "Max": None,
}

VALID_RANGES = set(_RANGE_DAYS.keys())


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _compute_cutoff_date(range_str: str) -> str | None:
    """Return YYYY-MM-DD cutoff date for a range, or ``None`` for Max."""
    if range_str == "Max":
        return None
    if range_str == "YTD":
        return f"{datetime.now(_ET).year}-01-01"

    days = _RANGE_DAYS.get(range_str)
    if days is None:
        return None
    return (date.today() - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def is_symbol_cached(symbol: str) -> bool:
    """Check if daily_history has any rows for *symbol*."""
    session = await get_session()
    try:
        result = await session.execute(
            text("SELECT 1 FROM daily_history WHERE symbol = :symbol LIMIT 1"),
            {"symbol": symbol},
        )
        return result.first() is not None
    finally:
        await session.close()


async def get_all_cached_symbols() -> list[str]:
    """Return distinct symbols present in daily_history."""
    session = await get_session()
    try:
        result = await session.execute(
            text("SELECT DISTINCT symbol FROM daily_history"),
        )
        return [row[0] for row in result.all()]
    finally:
        await session.close()


async def get_latest_cached_date(symbol: str) -> str | None:
    """Return the most recent cached date for *symbol*, or ``None``."""
    session = await get_session()
    try:
        result = await session.execute(
            text(
                "SELECT date FROM daily_history "
                "WHERE symbol = :symbol ORDER BY date DESC LIMIT 1"
            ),
            {"symbol": symbol},
        )
        row = result.first()
        return row[0] if row else None
    finally:
        await session.close()


async def store_bars(symbol: str, bars: list[dict]) -> int:
    """Insert OHLCV bars into daily_history, skipping duplicates.

    Uses dialect-aware upsert: ``ON CONFLICT DO NOTHING`` (PostgreSQL)
    or ``INSERT OR IGNORE`` (SQLite).  Returns the number of rows passed
    for insertion (actual inserts may be fewer due to conflict skips).
    """
    if not bars:
        return 0

    session = await get_session()
    try:
        dialect = get_dialect()

        if dialect == "postgresql":
            stmt = text("""
                INSERT INTO daily_history
                    (symbol, date, open, high, low, close, volume)
                VALUES (:symbol, :date, :open, :high, :low, :close, :volume)
                ON CONFLICT (symbol, date) DO NOTHING
            """)
        else:
            stmt = text("""
                INSERT OR IGNORE INTO daily_history
                    (symbol, date, open, high, low, close, volume)
                VALUES (:symbol, :date, :open, :high, :low, :close, :volume)
            """)

        rows = [
            {
                "symbol": symbol,
                "date": bar["date"],
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": bar.get("close"),
                "volume": bar.get("volume"),
            }
            for bar in bars
        ]

        await session.execute(stmt, rows)
        await session.commit()
        return len(rows)
    except Exception:
        logger.exception("Failed to store %d bars for %s", len(bars), symbol)
        await session.rollback()
        return 0
    finally:
        await session.close()


async def query_cached_history(symbol: str, range_str: str = "Max") -> list[dict]:
    """Query daily_history for *symbol* filtered by *range_str*.

    Returns rows in ascending date order.
    """
    cutoff = _compute_cutoff_date(range_str)

    session = await get_session()
    try:
        if cutoff:
            result = await session.execute(
                text("""
                    SELECT date, open, high, low, close, volume
                    FROM daily_history
                    WHERE symbol = :symbol AND date >= :cutoff
                    ORDER BY date ASC
                """),
                {"symbol": symbol, "cutoff": cutoff},
            )
        else:
            result = await session.execute(
                text("""
                    SELECT date, open, high, low, close, volume
                    FROM daily_history
                    WHERE symbol = :symbol
                    ORDER BY date ASC
                """),
                {"symbol": symbol},
            )

        return [
            {
                "date": row["date"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
            for row in result.mappings().all()
        ]
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_or_fetch_history(
    provider: TwelveDataProvider,
    symbol: str,
    range_str: str = "1Y",
) -> list[dict]:
    """Return cached OHLCV data, fetching from Twelve Data on cache miss.

    1. If *symbol* not in cache → fetch full history → store → filter by range.
    2. If *symbol* in cache → query DB filtered by range.
    """
    if range_str not in VALID_RANGES:
        raise ValueError(
            f"Invalid range: {range_str!r}. "
            f"Must be one of: {', '.join(sorted(VALID_RANGES))}"
        )

    cached = await is_symbol_cached(symbol)

    if not cached:
        logger.info("Cache miss for %s — fetching full history", symbol)
        bars = await provider.get_full_history(symbol)
        if bars:
            stored = await store_bars(symbol, bars)
            logger.info("Stored %d bars for %s", stored, symbol)
        else:
            logger.warning("No history data returned for %s", symbol)
            return []

    return await query_cached_history(symbol, range_str)


async def backfill_symbols(
    provider: TwelveDataProvider,
    symbols: list[str],
) -> dict[str, bool]:
    """Backfill full history for a list of symbols with rate limiting.

    Returns ``{symbol: success_bool}``.  Intended to run as a background
    task at startup.
    """
    results: dict[str, bool] = {}

    for symbol in symbols:
        if await is_symbol_cached(symbol):
            logger.debug("Backfill: %s already cached, skipping", symbol)
            results[symbol] = True
            continue

        logger.info("Backfill: fetching history for %s", symbol)
        bars = await provider.get_full_history(symbol)

        if bars:
            stored = await store_bars(symbol, bars)
            results[symbol] = stored > 0
            logger.info("Backfill: stored %d bars for %s", stored, symbol)
        else:
            results[symbol] = False
            logger.warning("Backfill: no data for %s", symbol)

        await asyncio.sleep(_BACKFILL_DELAY_SECONDS)

    succeeded = sum(1 for v in results.values() if v)
    logger.info(
        "Backfill complete: %d/%d symbols succeeded", succeeded, len(symbols),
    )
    return results


async def daily_append_all(provider: TwelveDataProvider) -> dict[str, bool]:
    """Append latest bar(s) for all cached symbols.

    For each symbol with cached history, fetches bars since the last
    cached date and stores any new ones.  Rate-limited.
    """
    cached_symbols = await get_all_cached_symbols()
    if not cached_symbols:
        logger.info("daily_append_all: no cached symbols, nothing to do")
        return {}

    logger.info("daily_append_all: updating %d symbols", len(cached_symbols))
    results: dict[str, bool] = {}

    for symbol in cached_symbols:
        latest_date = await get_latest_cached_date(symbol)
        if not latest_date:
            results[symbol] = False
            continue

        bars = await provider.get_history_since(symbol, latest_date)

        if bars:
            new_bars = [b for b in bars if b["date"] > latest_date]
            if new_bars:
                stored = await store_bars(symbol, new_bars)
                results[symbol] = stored > 0
                logger.debug("daily_append: %s +%d bars", symbol, stored)
            else:
                results[symbol] = True
        else:
            results[symbol] = False
            logger.warning("daily_append: no data for %s", symbol)

        await asyncio.sleep(_DAILY_APPEND_DELAY_SECONDS)

    succeeded = sum(1 for v in results.values() if v)
    logger.info(
        "daily_append_all complete: %d/%d symbols updated",
        succeeded,
        len(cached_symbols),
    )
    return results
