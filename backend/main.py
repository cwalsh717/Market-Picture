"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from pathlib import Path

from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.config import ASSETS, FRED_SERIES, SYMBOL_ASSET_CLASS
from backend.db import close_db, get_session, init_db
from backend.jobs.daily_update import generate_close_summary, save_quotes
from backend.jobs.scheduler import start_scheduler, stop_scheduler
from backend.providers.fred import FredProvider
from backend.providers.twelve_data import TwelveDataProvider
from backend.services.history_cache import (
    VALID_RANGES,
    backfill_symbols,
    get_or_fetch_history,
)

logger = logging.getLogger(__name__)

# Flat symbol → display name lookup (Twelve Data + FRED + synthetic spread)
_SYMBOL_NAMES: dict[str, str] = {}
for _symbols in ASSETS.values():
    _SYMBOL_NAMES.update(_symbols)
_SYMBOL_NAMES.update(FRED_SERIES)
_SYMBOL_NAMES["SPREAD_2S10S"] = "2s10s Yield Spread"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup, clean up on shutdown."""
    await init_db()
    app.state.twelve_data = TwelveDataProvider()
    app.state.fred = FredProvider()
    app.state.scheduler = start_scheduler(
        twelve_data=app.state.twelve_data,
        fred=app.state.fred,
    )

    # Start background backfill for dashboard symbols (non-blocking)
    app.state.backfill_task = asyncio.create_task(
        _startup_backfill(app.state.twelve_data)
    )

    logger.info("Market Picture started")
    yield

    # Cancel backfill if still running
    if app.state.backfill_task and not app.state.backfill_task.done():
        app.state.backfill_task.cancel()
        try:
            await app.state.backfill_task
        except asyncio.CancelledError:
            pass

    stop_scheduler()
    await app.state.fred.close()
    await app.state.twelve_data.close()
    await close_db()
    logger.info("Market Picture stopped")


async def _startup_backfill(provider: TwelveDataProvider) -> None:
    """Background task: backfill history for all dashboard symbols."""
    try:
        await asyncio.sleep(5)  # let the server finish starting
        symbols = [sym for group in ASSETS.values() for sym in group]
        await backfill_symbols(provider, symbols)
    except asyncio.CancelledError:
        logger.info("Startup backfill cancelled")
    except Exception:
        logger.exception("Startup backfill failed")


app = FastAPI(title="Market Picture", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    """Return service health status."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/snapshot")
async def snapshot() -> dict:
    """Return the most recent price snapshot for all assets, grouped by asset class."""
    session = await get_session()
    try:
        result = await session.execute(
            text("""
                SELECT s.symbol, s.asset_class, s.price, s.change_pct,
                       s.change_abs, s.timestamp
                FROM market_snapshots s
                INNER JOIN (
                    SELECT symbol, MAX(id) AS max_id
                    FROM market_snapshots GROUP BY symbol
                ) latest ON s.id = latest.max_id
            """)
        )
        rows = result.mappings().all()
    finally:
        await session.close()

    groups: dict[str, list[dict]] = {}
    last_updated = ""

    for row in rows:
        asset_class = row["asset_class"]
        symbol = row["symbol"]
        entry = {
            "symbol": symbol,
            "name": _SYMBOL_NAMES.get(symbol, symbol),
            "price": row["price"],
            "change_pct": row["change_pct"],
            "change_abs": row["change_abs"],
            "timestamp": row["timestamp"],
        }
        groups.setdefault(asset_class, []).append(entry)
        if row["timestamp"] and row["timestamp"] > last_updated:
            last_updated = row["timestamp"]

    return {"last_updated": last_updated, "assets": groups}


@app.get("/api/history/{symbol:path}")
async def history(
    symbol: str,
    range_str: str = Query("1Y", alias="range"),
    period: str = Query(None),
) -> dict:
    """Return OHLCV history for a symbol, fetching on demand if needed.

    FRED symbols are served directly from the FRED provider.
    Twelve Data symbols are served from the daily_history cache, with
    automatic fetch-and-store on first request.

    Query params:
        range:  1D, 5D, 1W, 1M, 3M, 6M, 1Y, YTD, 5Y, Max (default 1Y)
        period: deprecated alias for range (backward compat)
    """
    # Backward compat: accept ?period= when ?range= is not provided
    effective_range = period if period and range_str == "1Y" else range_str

    if effective_range not in VALID_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid range. Must be one of: {', '.join(sorted(VALID_RANGES))}",
        )

    # FRED symbols: delegate to FRED provider (no caching needed)
    if symbol in FRED_SERIES or symbol == "SPREAD_2S10S":
        bars = await app.state.fred.get_history(symbol, effective_range)
        return {"symbol": symbol, "range": effective_range, "bars": bars}

    # Twelve Data symbols: use history cache
    bars = await get_or_fetch_history(
        provider=app.state.twelve_data,
        symbol=symbol,
        range_str=effective_range,
    )
    return {"symbol": symbol, "range": effective_range, "bars": bars}


@app.get("/api/summary")
async def summary() -> dict:
    """Return the latest market summary and regime data."""
    session = await get_session()
    try:
        result = await session.execute(
            text("SELECT * FROM summaries ORDER BY date DESC, id DESC LIMIT 1")
        )
        row = result.mappings().first()
    finally:
        await session.close()

    if row is None:
        raise HTTPException(status_code=404, detail="No summaries available yet")

    return {
        "date": row["date"],
        "period": row["period"],
        "summary_text": row["summary_text"],
        "regime": {
            "label": row["regime_label"],
            "reason": row["regime_reason"],
            "signals": _parse_json(row["regime_signals_json"]),
        },
    }


@app.get("/api/search/{ticker:path}")
async def search_ticker(ticker: str) -> dict:
    """Fetch a live quote for an arbitrary ticker via Twelve Data."""
    quote = await app.state.twelve_data.get_quote(ticker.upper())
    if not quote:
        raise HTTPException(
            status_code=404, detail=f"No data found for {ticker!r}"
        )

    return {
        "symbol": ticker.upper(),
        "price": quote["price"],
        "change_pct": quote["change_pct"],
        "change_abs": quote["change_abs"],
        "timestamp": quote["timestamp"],
    }


@app.get("/api/intraday/{symbol:path}")
async def intraday(symbol: str) -> dict:
    """Return 5-minute intraday bars for today.

    Used by the dashboard "Today" view for richer intraday sparklines.
    FRED symbols are not supported for intraday — returns empty bars.
    """
    if symbol in FRED_SERIES or symbol == "SPREAD_2S10S":
        return {"symbol": symbol, "bars": []}

    bars = await app.state.twelve_data.get_intraday(symbol)
    return {"symbol": symbol, "bars": bars}


_ET = ZoneInfo("US/Eastern")


def _parse_json(value: str | None) -> object:
    """Parse a JSON string, returning None on failure."""
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


@app.get("/api/narratives")
async def narratives(date: str = Query(..., description="Date in YYYY-MM-DD format")) -> dict:
    """Return all archived narratives for a specific date."""
    session = await get_session()
    try:
        result = await session.execute(
            text("""
                SELECT timestamp, narrative_type, regime_label,
                       narrative_text, signal_inputs, movers_snapshot
                FROM narrative_archive
                WHERE date = :date
                ORDER BY id
            """),
            {"date": date},
        )
        rows = result.mappings().all()
    finally:
        await session.close()

    return {
        "date": date,
        "narratives": [
            {
                "timestamp": row["timestamp"],
                "narrative_type": row["narrative_type"],
                "regime_label": row["regime_label"],
                "narrative_text": row["narrative_text"],
                "signal_inputs": _parse_json(row["signal_inputs"]),
                "movers_snapshot": _parse_json(row["movers_snapshot"]),
            }
            for row in rows
        ],
    }


@app.get("/api/narratives/recent")
async def narratives_recent(days: int = Query(7, ge=1, le=90)) -> dict:
    """Return archived narratives from the last N days."""
    cutoff_date = (datetime.now(_ET) - timedelta(days=days)).date().isoformat()

    session = await get_session()
    try:
        result = await session.execute(
            text("""
                SELECT timestamp, date, narrative_type, regime_label,
                       narrative_text, signal_inputs, movers_snapshot
                FROM narrative_archive
                WHERE date >= :cutoff_date
                ORDER BY date DESC, id DESC
            """),
            {"cutoff_date": cutoff_date},
        )
        rows = result.mappings().all()
    finally:
        await session.close()

    return {
        "days": days,
        "narratives": [
            {
                "timestamp": row["timestamp"],
                "date": row["date"],
                "narrative_type": row["narrative_type"],
                "regime_label": row["regime_label"],
                "narrative_text": row["narrative_text"],
                "signal_inputs": _parse_json(row["signal_inputs"]),
                "movers_snapshot": _parse_json(row["movers_snapshot"]),
            }
            for row in rows
        ],
    }


@app.get("/api/regime-history")
async def regime_history() -> dict:
    """Return regime labels for the last 90 days."""
    cutoff_date = (datetime.now(_ET) - timedelta(days=90)).date().isoformat()

    session = await get_session()
    try:
        result = await session.execute(
            text("""
                SELECT date, narrative_type, regime_label
                FROM narrative_archive
                WHERE date >= :cutoff_date
                ORDER BY date DESC, id DESC
            """),
            {"cutoff_date": cutoff_date},
        )
        rows = result.mappings().all()
    finally:
        await session.close()

    return {
        "history": [
            {
                "date": row["date"],
                "narrative_type": row["narrative_type"],
                "regime_label": row["regime_label"],
            }
            for row in rows
        ],
    }


@app.post("/api/admin/fetch-now")
async def fetch_now() -> dict:
    """Manually trigger a full data fetch + intelligence pipeline run."""
    results: dict[str, object] = {}

    # 1. Fetch all Twelve Data quotes (ignore market hours)
    td: TwelveDataProvider = app.state.twelve_data
    td_quotes = await td.get_all_quotes()
    td_saved = await save_quotes(td_quotes)
    results["twelve_data"] = {"fetched": len(td_quotes), "saved": td_saved}
    logger.info("fetch-now: Twelve Data — %d fetched, %d saved", len(td_quotes), td_saved)

    # 2. Fetch all FRED quotes
    fred: FredProvider = app.state.fred
    fred_quotes = await fred.get_all_quotes()
    fred_saved = await save_quotes(fred_quotes)
    results["fred"] = {"fetched": len(fred_quotes), "saved": fred_saved}
    logger.info("fetch-now: FRED — %d fetched, %d saved", len(fred_quotes), fred_saved)

    # 3. Run intelligence pipeline (regime + LLM summary)
    try:
        await generate_close_summary()
        results["summary"] = "ok"
        logger.info("fetch-now: close summary generated")
    except Exception:
        logger.exception("fetch-now: summary generation failed")
        results["summary"] = "error"

    return {"status": "ok", "results": results}


# ---------------------------------------------------------------------------
# Static file serving (frontend)
# ---------------------------------------------------------------------------
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
