"""Watchlist CRUD: add, remove, list, and reorder symbols for logged-in users."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from backend.auth import get_current_user
from backend.config import WATCHLIST_MAX_SIZE
from backend.db import get_dialect, get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AddSymbolRequest(BaseModel):
    symbol: str


class ReorderRequest(BaseModel):
    symbols: list[str]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("")
async def list_watchlist(user: dict = Depends(get_current_user)) -> dict:
    """Return the authenticated user's watchlist with latest price data."""
    user_id = int(user["sub"])

    session = await get_session()
    try:
        result = await session.execute(
            text("""
                SELECT w.symbol, w.added_at, w.display_order,
                       s.price, s.change_pct, s.change_abs,
                       s.fifty_two_week_high, s.fifty_two_week_low
                FROM watchlists w
                LEFT JOIN market_snapshots s ON s.symbol = w.symbol
                    AND s.id = (
                        SELECT MAX(ms.id)
                        FROM market_snapshots ms
                        WHERE ms.symbol = w.symbol
                    )
                WHERE w.user_id = :user_id
                ORDER BY w.display_order
            """),
            {"user_id": user_id},
        )
        rows = result.mappings().all()

        symbols = [
            {
                "symbol": row["symbol"],
                "price": row["price"],
                "change_pct": row["change_pct"],
                "change_abs": row["change_abs"],
                "added_at": row["added_at"],
                "display_order": row["display_order"],
                "fifty_two_week_high": row["fifty_two_week_high"],
                "fifty_two_week_low": row["fifty_two_week_low"],
            }
            for row in rows
        ]

        return {"symbols": symbols, "count": len(symbols)}
    finally:
        await session.close()


@router.post("")
async def add_symbol(
    body: AddSymbolRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Add a symbol to the authenticated user's watchlist."""
    user_id = int(user["sub"])
    symbol = body.symbol.strip().upper()

    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol must not be empty")

    session = await get_session()
    try:
        # Check current count
        count_result = await session.execute(
            text("SELECT COUNT(*) AS cnt FROM watchlists WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        count = count_result.mappings().first()["cnt"]

        if count >= WATCHLIST_MAX_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Watchlist is full (max {WATCHLIST_MAX_SIZE} symbols)",
            )

        # Check for duplicate
        dup_result = await session.execute(
            text(
                "SELECT id FROM watchlists WHERE user_id = :user_id AND symbol = :symbol"
            ),
            {"user_id": user_id, "symbol": symbol},
        )
        if dup_result.first() is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{symbol} is already in your watchlist",
            )

        # Get next display_order
        max_result = await session.execute(
            text(
                "SELECT COALESCE(MAX(display_order), -1) AS max_order "
                "FROM watchlists WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
        next_order = max_result.mappings().first()["max_order"] + 1

        now = datetime.now(timezone.utc).isoformat()
        await session.execute(
            text("""
                INSERT INTO watchlists (user_id, symbol, added_at, display_order)
                VALUES (:user_id, :symbol, :added_at, :display_order)
            """),
            {
                "user_id": user_id,
                "symbol": symbol,
                "added_at": now,
                "display_order": next_order,
            },
        )
        await session.commit()

        return {"status": "ok", "symbol": symbol}
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception("Failed to add symbol to watchlist")
        raise HTTPException(status_code=500, detail="Failed to add symbol")
    finally:
        await session.close()


@router.delete("/{symbol:path}")
async def remove_symbol(
    symbol: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Remove a symbol from the authenticated user's watchlist."""
    user_id = int(user["sub"])
    symbol = symbol.strip().upper()

    session = await get_session()
    try:
        result = await session.execute(
            text(
                "DELETE FROM watchlists WHERE user_id = :user_id AND symbol = :symbol"
            ),
            {"user_id": user_id, "symbol": symbol},
        )
        await session.commit()

        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail=f"{symbol} not found in your watchlist",
            )

        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception("Failed to remove symbol from watchlist")
        raise HTTPException(status_code=500, detail="Failed to remove symbol")
    finally:
        await session.close()


@router.put("/reorder")
async def reorder_watchlist(
    body: ReorderRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Reorder the authenticated user's watchlist symbols."""
    user_id = int(user["sub"])

    session = await get_session()
    try:
        for i, symbol in enumerate(body.symbols):
            await session.execute(
                text(
                    "UPDATE watchlists SET display_order = :order "
                    "WHERE user_id = :user_id AND symbol = :symbol"
                ),
                {"order": i, "user_id": user_id, "symbol": symbol.strip().upper()},
            )
        await session.commit()

        return {"status": "ok"}
    except Exception:
        await session.rollback()
        logger.exception("Failed to reorder watchlist")
        raise HTTPException(status_code=500, detail="Failed to reorder watchlist")
    finally:
        await session.close()


@router.post("/{symbol:path}/analysis")
async def company_analysis(
    symbol: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Generate on-demand company analysis. Cached per (symbol, user_id, date)."""
    user_id = int(user["sub"])
    symbol_upper = symbol.strip().upper()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check cache first
    session = await get_session()
    try:
        result = await session.execute(
            text("""
                SELECT analysis_text FROM company_analyses
                WHERE symbol = :symbol AND user_id = :user_id AND date = :date
            """),
            {"symbol": symbol_upper, "user_id": user_id, "date": today},
        )
        cached = result.mappings().first()
        if cached:
            return {
                "symbol": symbol_upper,
                "date": today,
                "analysis": cached["analysis_text"],
                "cached": True,
            }
    finally:
        await session.close()

    # Generate new analysis via Claude API
    from backend.intelligence.company_analysis import generate_company_analysis as gen_analysis

    try:
        analysis_text = await gen_analysis(symbol_upper)
    except Exception:
        logger.exception("Company analysis failed for %s", symbol_upper)
        raise HTTPException(
            status_code=503,
            detail="Analysis generation failed. Try again later.",
        )

    # Cache the result (dialect-aware upsert)
    now_iso = datetime.now(timezone.utc).isoformat()
    session = await get_session()
    try:
        dialect = get_dialect()
        if dialect == "postgresql":
            await session.execute(
                text("""
                    INSERT INTO company_analyses
                        (symbol, user_id, date, analysis_text, created_at)
                    VALUES (:symbol, :user_id, :date, :text, :created_at)
                    ON CONFLICT (symbol, user_id, date) DO UPDATE SET
                        analysis_text = EXCLUDED.analysis_text,
                        created_at = EXCLUDED.created_at
                """),
                {
                    "symbol": symbol_upper,
                    "user_id": user_id,
                    "date": today,
                    "text": analysis_text,
                    "created_at": now_iso,
                },
            )
        else:
            await session.execute(
                text("""
                    INSERT OR REPLACE INTO company_analyses
                        (symbol, user_id, date, analysis_text, created_at)
                    VALUES (:symbol, :user_id, :date, :text, :created_at)
                """),
                {
                    "symbol": symbol_upper,
                    "user_id": user_id,
                    "date": today,
                    "text": analysis_text,
                    "created_at": now_iso,
                },
            )
        await session.commit()
    except Exception:
        logger.exception("Failed to cache company analysis for %s", symbol_upper)
        await session.rollback()
        # Still return the analysis even if caching fails
    finally:
        await session.close()

    return {
        "symbol": symbol_upper,
        "date": today,
        "analysis": analysis_text,
        "cached": False,
    }
