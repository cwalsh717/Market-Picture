"""Hierarchical watchlist CRUD: lists and items for logged-in users."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from backend.auth import get_current_user
from backend.config import WATCHLIST_MAX_ITEMS_PER_LIST, WATCHLIST_MAX_LISTS
from backend.db import get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CreateListRequest(BaseModel):
    name: str


class UpdateListRequest(BaseModel):
    name: Optional[str] = None
    position: Optional[int] = None


class AddItemRequest(BaseModel):
    symbol: str


class ReorderItemsRequest(BaseModel):
    symbols: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _verify_ownership(session, watchlist_id: int, user_id: int) -> None:
    """Verify the watchlist belongs to the user. Raises 404 if not found or not owned."""
    result = await session.execute(
        text("SELECT id FROM watchlist_lists WHERE id = :id AND user_id = :user_id"),
        {"id": watchlist_id, "user_id": user_id},
    )
    if result.first() is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


@router.get("")
async def list_watchlists(user: dict = Depends(get_current_user)) -> dict:
    """Return all watchlists and their items for the authenticated user."""
    user_id = int(user["sub"])

    session = await get_session()
    try:
        # Fetch all lists for this user
        lists_result = await session.execute(
            text(
                "SELECT id, name, position, is_default "
                "FROM watchlist_lists "
                "WHERE user_id = :user_id "
                "ORDER BY position"
            ),
            {"user_id": user_id},
        )
        lists_rows = lists_result.mappings().all()

        watchlists = []
        for wl_row in lists_rows:
            wl_id = wl_row["id"]

            # Fetch items for this list
            items_result = await session.execute(
                text(
                    "SELECT symbol, position "
                    "FROM watchlist_items "
                    "WHERE watchlist_id = :wl_id "
                    "ORDER BY position"
                ),
                {"wl_id": wl_id},
            )
            items = [
                {"symbol": item["symbol"], "position": item["position"]}
                for item in items_result.mappings().all()
            ]

            watchlists.append({
                "id": wl_id,
                "name": wl_row["name"],
                "position": wl_row["position"],
                "is_default": wl_row["is_default"],
                "items": items,
            })

        return {"watchlists": watchlists}
    finally:
        await session.close()


@router.post("")
async def create_watchlist(
    body: CreateListRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Create a new empty watchlist for the authenticated user."""
    user_id = int(user["sub"])
    name = body.name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="Watchlist name must not be empty")

    session = await get_session()
    try:
        # Check list count
        count_result = await session.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM watchlist_lists WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
        count = count_result.mappings().first()["cnt"]

        if count >= WATCHLIST_MAX_LISTS:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum number of watchlists reached ({WATCHLIST_MAX_LISTS})",
            )

        # Get next position
        max_result = await session.execute(
            text(
                "SELECT COALESCE(MAX(position), -1) AS max_pos "
                "FROM watchlist_lists WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
        next_pos = max_result.mappings().first()["max_pos"] + 1

        now = datetime.now(timezone.utc).isoformat()

        # Insert via ORM to get the new ID back portably
        from backend.db import WatchlistList

        wl = WatchlistList(
            user_id=user_id,
            name=name,
            position=next_pos,
            is_default=0,
            created_at=now,
        )
        session.add(wl)
        await session.flush()
        await session.refresh(wl)
        await session.commit()

        return {"id": wl.id, "name": name, "position": next_pos}
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception("Failed to create watchlist")
        raise HTTPException(status_code=500, detail="Failed to create watchlist")
    finally:
        await session.close()


@router.put("/{watchlist_id}")
async def update_watchlist(
    watchlist_id: int,
    body: UpdateListRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Update the name and/or position of a watchlist."""
    user_id = int(user["sub"])

    session = await get_session()
    try:
        await _verify_ownership(session, watchlist_id, user_id)

        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(
                    status_code=400, detail="Watchlist name must not be empty"
                )
            await session.execute(
                text(
                    "UPDATE watchlist_lists SET name = :name "
                    "WHERE id = :id AND user_id = :user_id"
                ),
                {"name": name, "id": watchlist_id, "user_id": user_id},
            )

        if body.position is not None:
            await session.execute(
                text(
                    "UPDATE watchlist_lists SET position = :position "
                    "WHERE id = :id AND user_id = :user_id"
                ),
                {"position": body.position, "id": watchlist_id, "user_id": user_id},
            )

        await session.commit()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception("Failed to update watchlist")
        raise HTTPException(status_code=500, detail="Failed to update watchlist")
    finally:
        await session.close()


@router.delete("/{watchlist_id}")
async def delete_watchlist(
    watchlist_id: int,
    user: dict = Depends(get_current_user),
) -> dict:
    """Delete a watchlist and all its items."""
    user_id = int(user["sub"])

    session = await get_session()
    try:
        await _verify_ownership(session, watchlist_id, user_id)

        # Delete all items first
        await session.execute(
            text("DELETE FROM watchlist_items WHERE watchlist_id = :wl_id"),
            {"wl_id": watchlist_id},
        )

        # Delete the list itself
        await session.execute(
            text(
                "DELETE FROM watchlist_lists WHERE id = :id AND user_id = :user_id"
            ),
            {"id": watchlist_id, "user_id": user_id},
        )

        await session.commit()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception("Failed to delete watchlist")
        raise HTTPException(status_code=500, detail="Failed to delete watchlist")
    finally:
        await session.close()


@router.post("/{watchlist_id}/items")
async def add_item(
    watchlist_id: int,
    body: AddItemRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Add a ticker to a watchlist."""
    user_id = int(user["sub"])
    symbol = body.symbol.strip().upper()

    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol must not be empty")

    session = await get_session()
    try:
        await _verify_ownership(session, watchlist_id, user_id)

        # Check item count
        count_result = await session.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM watchlist_items "
                "WHERE watchlist_id = :wl_id"
            ),
            {"wl_id": watchlist_id},
        )
        count = count_result.mappings().first()["cnt"]

        if count >= WATCHLIST_MAX_ITEMS_PER_LIST:
            raise HTTPException(
                status_code=400,
                detail=f"Watchlist is full (max {WATCHLIST_MAX_ITEMS_PER_LIST} items)",
            )

        # Check for duplicate
        dup_result = await session.execute(
            text(
                "SELECT id FROM watchlist_items "
                "WHERE watchlist_id = :wl_id AND symbol = :symbol"
            ),
            {"wl_id": watchlist_id, "symbol": symbol},
        )
        if dup_result.first() is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{symbol} is already in this watchlist",
            )

        # Get next position
        max_result = await session.execute(
            text(
                "SELECT COALESCE(MAX(position), -1) AS max_pos "
                "FROM watchlist_items WHERE watchlist_id = :wl_id"
            ),
            {"wl_id": watchlist_id},
        )
        next_pos = max_result.mappings().first()["max_pos"] + 1

        now = datetime.now(timezone.utc).isoformat()
        await session.execute(
            text(
                "INSERT INTO watchlist_items (watchlist_id, symbol, position, added_at) "
                "VALUES (:wl_id, :symbol, :position, :added_at)"
            ),
            {
                "wl_id": watchlist_id,
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
        logger.exception("Failed to add item to watchlist")
        raise HTTPException(status_code=500, detail="Failed to add item")
    finally:
        await session.close()


@router.delete("/{watchlist_id}/items/{symbol:path}")
async def remove_item(
    watchlist_id: int,
    symbol: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Remove a ticker from a watchlist."""
    user_id = int(user["sub"])
    symbol = symbol.strip().upper()

    session = await get_session()
    try:
        await _verify_ownership(session, watchlist_id, user_id)

        result = await session.execute(
            text(
                "DELETE FROM watchlist_items "
                "WHERE watchlist_id = :wl_id AND symbol = :symbol"
            ),
            {"wl_id": watchlist_id, "symbol": symbol},
        )
        await session.commit()

        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail=f"{symbol} not found in this watchlist",
            )

        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception("Failed to remove item from watchlist")
        raise HTTPException(status_code=500, detail="Failed to remove item")
    finally:
        await session.close()


@router.put("/{watchlist_id}/items/reorder")
async def reorder_items(
    watchlist_id: int,
    body: ReorderItemsRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Reorder items within a watchlist."""
    user_id = int(user["sub"])

    session = await get_session()
    try:
        await _verify_ownership(session, watchlist_id, user_id)

        for i, symbol in enumerate(body.symbols):
            await session.execute(
                text(
                    "UPDATE watchlist_items SET position = :position "
                    "WHERE watchlist_id = :wl_id AND symbol = :symbol"
                ),
                {
                    "position": i,
                    "wl_id": watchlist_id,
                    "symbol": symbol.strip().upper(),
                },
            )
        await session.commit()

        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception("Failed to reorder watchlist items")
        raise HTTPException(status_code=500, detail="Failed to reorder items")
    finally:
        await session.close()


@router.get("/{watchlist_id}/prices")
async def watchlist_prices(
    watchlist_id: int,
    user: dict = Depends(get_current_user),
) -> dict:
    """Return price data for all symbols in a watchlist."""
    user_id = int(user["sub"])

    session = await get_session()
    try:
        await _verify_ownership(session, watchlist_id, user_id)

        result = await session.execute(
            text("""
                SELECT wi.symbol,
                       COALESCE(s.price, dh.close) AS price,
                       s.change_pct,
                       s.change_abs
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
            {"wl_id": watchlist_id},
        )
        rows = result.mappings().all()

        items = [
            {
                "symbol": row["symbol"],
                "price": row["price"],
                "change_pct": row["change_pct"],
                "change_abs": row["change_abs"],
            }
            for row in rows
        ]

        return {"items": items}
    finally:
        await session.close()
