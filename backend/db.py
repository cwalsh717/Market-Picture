"""Database setup with SQLAlchemy async engine (PostgreSQL or SQLite fallback)."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import Float, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine + session factory (lazy-initialized by init_db)
# ---------------------------------------------------------------------------

_engine = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _build_url() -> str:
    """Determine async database URL from environment.

    Converts standard dialect URLs to their async equivalents so that
    ``create_async_engine`` receives a valid driver string.
    """
    from backend.config import DATABASE_URL, DATABASE_PATH

    if DATABASE_URL:
        url = DATABASE_URL
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        if url.startswith("sqlite:///") and "+aiosqlite" not in url:
            return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return url

    # Fallback to SQLite for local dev
    return f"sqlite+aiosqlite:///{DATABASE_PATH}"


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_abs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    timestamp: Mapped[str] = mapped_column(String, nullable=False)
    average_volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fifty_two_week_high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fifty_two_week_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fifty_two_week_high_change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fifty_two_week_low_change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rolling_1d_change: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rolling_7d_change: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class DailyHistory(Base):
    __tablename__ = "daily_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)
    open: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (UniqueConstraint("symbol", "date"),)


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String, nullable=False)
    period: Mapped[str] = mapped_column(String, nullable=False)
    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    regime_label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    regime_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    regime_signals_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class SearchCache(Base):
    __tablename__ = "search_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[str] = mapped_column(String, nullable=False)


class NarrativeArchive(Base):
    __tablename__ = "narrative_archive"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)
    narrative_type: Mapped[str] = mapped_column(String(20), nullable=False)
    regime_label: Mapped[str] = mapped_column(String(20), nullable=False)
    narrative_text: Mapped[str] = mapped_column(Text, nullable=False)
    signal_inputs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    movers_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    added_at: Mapped[str] = mapped_column(String, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)


class TechnicalSignal(Base):
    __tablename__ = "technical_signals"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[str] = mapped_column(String, primary_key=True)
    rsi_14: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    atr_14: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_50: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_200: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


# ---------------------------------------------------------------------------
# Initialization + session access
# ---------------------------------------------------------------------------


async def init_db(url: Optional[str] = None) -> None:
    """Initialize the async engine, session factory, and create all tables.

    Args:
        url: Explicit database URL. If None, auto-detects from env vars
             (DATABASE_URL for PostgreSQL, DATABASE_PATH for SQLite fallback).
    """
    global _engine, _session_factory

    actual_url = url or _build_url()
    _engine = create_async_engine(actual_url, echo=False)
    _session_factory = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False,
    )

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _run_migrations()

    dialect = _engine.dialect.name
    safe_url = actual_url.split("@")[-1] if "@" in actual_url else actual_url
    logger.info("Database initialized (%s): %s", dialect, safe_url)


async def get_session() -> AsyncSession:
    """Return a new async session. Requires prior ``init_db()`` call."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _session_factory()


def get_dialect() -> str:
    """Return the dialect name of the current engine (e.g. 'sqlite', 'postgresql')."""
    if _engine is None:
        return "sqlite"
    return _engine.dialect.name


async def close_db() -> None:
    """Dispose of the engine and reset module state."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def _run_migrations() -> None:
    """Add columns that may be missing from older schemas.

    Safe to run repeatedly — each ALTER TABLE is wrapped in try/except
    so columns that already exist are silently skipped.
    """
    if _session_factory is None:
        return

    session = _session_factory()
    try:
        # market_snapshots: enriched quote columns
        for col in (
            "average_volume",
            "fifty_two_week_high",
            "fifty_two_week_low",
            "fifty_two_week_high_change_pct",
            "fifty_two_week_low_change_pct",
            "rolling_1d_change",
            "rolling_7d_change",
        ):
            try:
                await session.execute(
                    text(
                        f"ALTER TABLE market_snapshots ADD COLUMN {col} DOUBLE PRECISION"
                    )
                )
                await session.commit()
            except Exception:
                await session.rollback()

        # summaries: regime_signals_json (legacy migration)
        try:
            await session.execute(
                text("ALTER TABLE summaries ADD COLUMN regime_signals_json TEXT")
            )
            await session.commit()
        except Exception:
            await session.rollback()
    finally:
        await session.close()
