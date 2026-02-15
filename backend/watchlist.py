"""Backward-compat shim: /api/watchlist (singular) delegates to the user's default watchlist."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from backend.auth import get_current_user
from backend.config import WATCHLIST_MAX_ITEMS_PER_LIST
from backend.db import get_dialect, get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models (kept for backward compat)
# ---------------------------------------------------------------------------


class AddSymbolRequest(BaseModel):
    symbol: str


class ReorderRequest(BaseModel):
    symbols: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_default_watchlist_id(user_id: int, session) -> int:
    """Get the user's default watchlist ID, creating one if needed."""
    result = await session.execute(
        text("SELECT id FROM watchlist_lists WHERE user_id = :user_id AND is_default = 1"),
        {"user_id": user_id},
    )
    row = result.first()
    if row:
        return row[0]

    # Auto-create default watchlist with Mag 7
    from backend.db import seed_default_watchlist

    await seed_default_watchlist(user_id, session)
    await session.commit()

    result = await session.execute(
        text("SELECT id FROM watchlist_lists WHERE user_id = :user_id AND is_default = 1"),
        {"user_id": user_id},
    )
    return result.first()[0]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("")
async def list_watchlist(user: dict = Depends(get_current_user)) -> dict:
    """Return the authenticated user's default watchlist with latest price data."""
    user_id = int(user["sub"])

    session = await get_session()
    try:
        wl_id = await _get_default_watchlist_id(user_id, session)

        result = await session.execute(
            text("""
                SELECT wi.symbol, wi.added_at, wi.position,
                       COALESCE(s.price, dh.close) AS price,
                       s.change_pct, s.change_abs,
                       s.fifty_two_week_high, s.fifty_two_week_low
                FROM watchlist_items wi
                LEFT JOIN market_snapshots s ON s.symbol = wi.symbol
                    AND s.id = (
                        SELECT MAX(ms.id)
                        FROM market_snapshots ms
                        WHERE ms.symbol = wi.symbol
                    )
                LEFT JOIN daily_history dh ON dh.symbol = wi.symbol
                    AND dh.date = (
                        SELECT MAX(dh2.date)
                        FROM daily_history dh2
                        WHERE dh2.symbol = wi.symbol
                    )
                WHERE wi.watchlist_id = :wl_id
                ORDER BY wi.position
            """),
            {"wl_id": wl_id},
        )
        rows = result.mappings().all()

        symbols = [
            {
                "symbol": row["symbol"],
                "price": row["price"],
                "change_pct": row["change_pct"],
                "change_abs": row["change_abs"],
                "added_at": row["added_at"],
                "display_order": row["position"],
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
    """Add a symbol to the authenticated user's default watchlist."""
    user_id = int(user["sub"])
    symbol = body.symbol.strip().upper()

    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol must not be empty")

    session = await get_session()
    try:
        wl_id = await _get_default_watchlist_id(user_id, session)

        # Check current count
        count_result = await session.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM watchlist_items WHERE watchlist_id = :wl_id"
            ),
            {"wl_id": wl_id},
        )
        count = count_result.mappings().first()["cnt"]

        if count >= WATCHLIST_MAX_ITEMS_PER_LIST:
            raise HTTPException(
                status_code=400,
                detail=f"Watchlist is full (max {WATCHLIST_MAX_ITEMS_PER_LIST} symbols)",
            )

        # Check for duplicate
        dup_result = await session.execute(
            text(
                "SELECT id FROM watchlist_items "
                "WHERE watchlist_id = :wl_id AND symbol = :symbol"
            ),
            {"wl_id": wl_id, "symbol": symbol},
        )
        if dup_result.first() is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{symbol} is already in your watchlist",
            )

        # Get next position
        max_result = await session.execute(
            text(
                "SELECT COALESCE(MAX(position), -1) AS max_pos "
                "FROM watchlist_items WHERE watchlist_id = :wl_id"
            ),
            {"wl_id": wl_id},
        )
        next_pos = max_result.mappings().first()["max_pos"] + 1

        now = datetime.now(timezone.utc).isoformat()
        await session.execute(
            text(
                "INSERT INTO watchlist_items (watchlist_id, symbol, position, added_at) "
                "VALUES (:wl_id, :symbol, :position, :added_at)"
            ),
            {
                "wl_id": wl_id,
                "symbol": symbol,
                "position": next_pos,
                "added_at": now,
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
    """Remove a symbol from the authenticated user's default watchlist."""
    user_id = int(user["sub"])
    symbol = symbol.strip().upper()

    session = await get_session()
    try:
        wl_id = await _get_default_watchlist_id(user_id, session)

        result = await session.execute(
            text(
                "DELETE FROM watchlist_items "
                "WHERE watchlist_id = :wl_id AND symbol = :symbol"
            ),
            {"wl_id": wl_id, "symbol": symbol},
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
    """Reorder the authenticated user's default watchlist symbols."""
    user_id = int(user["sub"])

    session = await get_session()
    try:
        wl_id = await _get_default_watchlist_id(user_id, session)

        for i, symbol in enumerate(body.symbols):
            await session.execute(
                text(
                    "UPDATE watchlist_items SET position = :position "
                    "WHERE watchlist_id = :wl_id AND symbol = :symbol"
                ),
                {
                    "position": i,
                    "wl_id": wl_id,
                    "symbol": symbol.strip().upper(),
                },
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
