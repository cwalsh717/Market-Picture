"""FastAPI application entry point."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import ASSETS, FRED_SERIES, SYMBOL_ASSET_CLASS
from backend.db import get_connection, init_db
from backend.jobs.daily_update import generate_close_summary, save_quotes
from backend.jobs.scheduler import start_scheduler, stop_scheduler
from backend.providers.fred import FredProvider
from backend.providers.twelve_data import TwelveDataProvider

logger = logging.getLogger(__name__)

# Flat symbol → display name lookup (Twelve Data + FRED + synthetic spread)
_SYMBOL_NAMES: dict[str, str] = {}
for _symbols in ASSETS.values():
    _SYMBOL_NAMES.update(_symbols)
_SYMBOL_NAMES.update(FRED_SERIES)
_SYMBOL_NAMES["SPREAD_2S10S"] = "2s10s Yield Spread"

_VALID_PERIODS = {"1D", "1W", "1M", "YTD"}


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
    logger.info("Market Picture started")
    yield
    stop_scheduler()
    await app.state.fred.close()
    await app.state.twelve_data.close()
    logger.info("Market Picture stopped")


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
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT s.symbol, s.asset_class, s.price, s.change_pct,
                   s.change_abs, s.timestamp
            FROM market_snapshots s
            INNER JOIN (
                SELECT symbol, MAX(id) AS max_id
                FROM market_snapshots GROUP BY symbol
            ) latest ON s.id = latest.max_id
            """
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

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
async def history(symbol: str, period: str = "1W") -> dict:
    """Return sparkline data (date + close) for a symbol over a given period."""
    if period not in _VALID_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid period. Must be one of: {', '.join(sorted(_VALID_PERIODS))}",
        )

    if symbol in FRED_SERIES or symbol == "SPREAD_2S10S":
        provider = app.state.fred
    else:
        provider = app.state.twelve_data

    bars = await provider.get_history(symbol, period)
    return {
        "symbol": symbol,
        "period": period,
        "bars": [{"date": b["date"], "close": b["close"]} for b in bars],
    }


@app.get("/api/summary")
async def summary() -> dict:
    """Return the latest market summary, regime, and correlation data."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT * FROM summaries ORDER BY date DESC, id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="No summaries available yet")

    def _parse_json(value: str | None) -> object:
        if not value:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None

    return {
        "date": row["date"],
        "period": row["period"],
        "summary_text": row["summary_text"],
        "regime": {
            "label": row["regime_label"],
            "reason": row["regime_reason"],
            "signals": _parse_json(row["regime_signals_json"]),
        },
        "moving_together": _parse_json(row["moving_together_json"]),
        "correlations": _parse_json(row["correlations_json"]),
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

    # 3. Run intelligence pipeline (regime + correlations + LLM summary)
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
