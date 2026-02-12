"""Async SQLite database setup and initialization."""

import aiosqlite

from backend.config import DATABASE_PATH

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    asset_class TEXT    NOT NULL,
    price       REAL    NOT NULL,
    change_pct  REAL,
    change_abs  REAL,
    timestamp   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_history (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT    NOT NULL,
    date   TEXT    NOT NULL,
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume INTEGER,
    UNIQUE(symbol, date)
);

CREATE TABLE IF NOT EXISTS summaries (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    date                 TEXT    NOT NULL,
    period               TEXT    NOT NULL,
    summary_text         TEXT,
    regime_label         TEXT,
    regime_reason        TEXT,
    regime_signals_json  TEXT
);

CREATE TABLE IF NOT EXISTS search_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT NOT NULL,
    result_json TEXT,
    timestamp   TEXT NOT NULL
);
"""


async def get_connection() -> aiosqlite.Connection:
    """Open a connection to the SQLite database."""
    conn = await aiosqlite.connect(DATABASE_PATH)
    conn.row_factory = aiosqlite.Row
    return conn


async def _migrate_summaries_table(conn: aiosqlite.Connection) -> None:
    """Add columns that may be missing from an older summaries schema."""
    cursor = await conn.execute("PRAGMA table_info(summaries)")
    existing = {row[1] for row in await cursor.fetchall()}

    if "regime_signals_json" not in existing:
        await conn.execute(
            "ALTER TABLE summaries ADD COLUMN regime_signals_json TEXT"
        )


async def init_db() -> None:
    """Create all tables if they don't already exist."""
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.executescript(_CREATE_TABLES)
        await _migrate_summaries_table(conn)
        await conn.commit()
