"""SQLite connection helper and cached reference time; raw SQL only, no ORM."""

import sqlite3
from functools import lru_cache

from app.config import SQLITE_PATH


def connect() -> sqlite3.Connection:
    if not SQLITE_PATH.exists():
        raise RuntimeError(f"{SQLITE_PATH} not found. Run: python scripts/load_structured_data.py")
    conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql: str, params: dict) -> list[dict]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


@lru_cache(maxsize=1)
def reference_time() -> str:
    """Dataset snapshot from the workbook README — the 'now' for every time-based answer."""
    rows = query("SELECT value FROM meta WHERE key = 'reference_time'", {})
    return rows[0]["value"]
