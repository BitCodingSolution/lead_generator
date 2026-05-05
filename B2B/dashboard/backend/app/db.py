"""SQLite helpers for the Marcel leads.db.

Thin wrappers around `sqlite3` that match the q_one/q_all idiom used
throughout the legacy main.py. All paths are derived from settings so a
relocation of the data directory only requires an env var change.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.config import settings


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(settings.db_path))
    c.row_factory = sqlite3.Row
    return c


def q_one(sql: str, *params: Any):
    """Run `sql` and return the first column of the first row, or 0 if empty.

    Mirrors legacy semantics — most call sites use it as a COUNT() helper.
    """
    c = conn()
    try:
        r = c.execute(sql, params).fetchone()
        return r[0] if r else 0
    finally:
        c.close()


def q_all(sql: str, *params: Any) -> list[dict]:
    c = conn()
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()
