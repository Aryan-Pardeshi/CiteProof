import logging
import os
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

# Base cumulative count of research-mode "papers" launched by users.
BASE_PAPER_COUNT = int(os.getenv("INITIAL_PAPER_COUNT", "33"))


def _stats_db_path() -> Path:
    base = os.getenv("RESEARCH_DB_PATH", "./data/research_state.db")
    return Path(base).resolve().parent / "site_stats.db"


async def _connect() -> aiosqlite.Connection:
    db_path = _stats_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    # WAL lets a reader and writer proceed without blocking each other, and
    # busy_timeout avoids "database is locked" spikes under rare concurrent writes.
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute(
        "CREATE TABLE IF NOT EXISTS paper_stats("
        "id INTEGER PRIMARY KEY CHECK (id=1), "
        f"total INTEGER NOT NULL DEFAULT {BASE_PAPER_COUNT})"
    )
    await db.execute(
        "INSERT OR IGNORE INTO paper_stats(id, total) VALUES(1, ?)",
        (BASE_PAPER_COUNT,)
    )
    # Ensure any database with total < BASE_PAPER_COUNT is upgraded to BASE_PAPER_COUNT
    await db.execute(
        "UPDATE paper_stats SET total = ? WHERE id = 1 AND total < ?",
        (BASE_PAPER_COUNT, BASE_PAPER_COUNT)
    )
    await db.commit()
    return db


async def init_stats_db() -> None:
    """Warm the stats database at startup (called from the app lifespan)."""
    db = await _connect()
    await db.close()


async def increment_paper_count() -> int:
    """Increment the cumulative paper count and return the new total."""
    db = await _connect()
    try:
        cursor = await db.execute(
            "UPDATE paper_stats SET total = CASE WHEN total < ? THEN ? + 1 ELSE total + 1 END WHERE id = 1 RETURNING total",
            (BASE_PAPER_COUNT, BASE_PAPER_COUNT)
        )
        row = await cursor.fetchone()
        await db.commit()
        return row[0] if row else BASE_PAPER_COUNT + 1
    finally:
        await db.close()


async def get_paper_count() -> int:
    """Return the current cumulative paper count (BASE_PAPER_COUNT minimum)."""
    db = await _connect()
    try:
        cursor = await db.execute("SELECT total FROM paper_stats WHERE id = 1")
        row = await cursor.fetchone()
        val = row[0] if row else BASE_PAPER_COUNT
        return max(val, BASE_PAPER_COUNT)
    finally:
        await db.close()
