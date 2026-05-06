"""Postgres helpers for the Marcel data — drop-in replacement for the
prior SQLite (`leads.db`) wrappers.

Public contract (`conn`, `q_one`, `q_all`) is unchanged so every router
and service that already uses these helpers keeps working without edits.

Behind the scenes:
- Shares the SQLAlchemy engine with the linkedin module — one connection
  pool per process. The on-disk Marcel SQLite file is no longer touched.
- Reuses linkedin's `PgConnectionFixed` adapter, which gives us the
  sqlite-compat surface the existing call sites expect: `?` placeholders
  → `%s`, `INSERT OR REPLACE` → `ON CONFLICT … DO UPDATE`, dict-row
  results, etc.
- Rewrites legacy Marcel table names (`leads`, `lead_status`, …) to
  their `mrc_`-prefixed Postgres counterparts on every query so call
  sites don't have to be updated.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator

from app.linkedin.db import PgConnectionFixed, get_engine, SessionLocal


# Marcel SQLite table names → their Postgres `mrc_` counterparts.
# Word-boundary regexes below only match a bare legacy name when it
# appears immediately after a SQL operator keyword, so naturally-
# occurring words like 'leads' or 'notes' inside identifier-quoted
# columns or English text are left alone.
_TABLE_MAP: dict[str, str] = {
    "leads": "mrc_leads",
    "lead_status": "mrc_lead_status",
    "emails_sent": "mrc_emails_sent",
    "replies": "mrc_replies",
    "daily_batches": "mrc_daily_batches",
    "deals": "mrc_deals",
    "do_not_contact": "mrc_do_not_contact",
    "meetings": "mrc_meetings",
    "notes": "mrc_notes",
}
_TABLES_RE = "|".join(re.escape(t) for t in _TABLE_MAP)
_KEYWORD_BEFORE = re.compile(
    rf"(\b(?:FROM|INTO|UPDATE|JOIN|REFERENCES)\s+)({_TABLES_RE})\b",
    re.IGNORECASE,
)
_TABLE_BEFORE = re.compile(
    rf"(\bTABLE\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?)({_TABLES_RE})\b",
    re.IGNORECASE,
)


def _rewrite_marcel_sql(sql: str) -> str:
    """Replace Marcel legacy table names in `sql` with their mrc_* names."""
    sql = _KEYWORD_BEFORE.sub(
        lambda m: f"{m.group(1)}{_TABLE_MAP[m.group(2).lower()]}", sql,
    )
    sql = _TABLE_BEFORE.sub(
        lambda m: f"{m.group(1)}{_TABLE_MAP[m.group(2).lower()]}", sql,
    )
    return sql


class _MarcelConnection:
    """Wraps `PgConnectionFixed` and pre-rewrites every SQL string so
    legacy Marcel table names route to their `mrc_` counterparts in
    Postgres. Mimics enough of `sqlite3.Connection` for the existing
    call sites to keep functioning unchanged.
    """

    def __init__(self, inner: PgConnectionFixed) -> None:
        self._inner = inner

    def execute(self, sql: str, params: tuple = ()):
        return self._inner.execute(_rewrite_marcel_sql(sql), params)

    def executemany(self, sql: str, params_list):
        return self._inner.executemany(_rewrite_marcel_sql(sql), params_list)

    def commit(self) -> None:
        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()

    def close(self) -> None:
        self._inner.close()


@contextmanager
def conn() -> Iterator[_MarcelConnection]:
    """Open a Postgres-backed connection for Marcel queries.

    Identical context-manager semantics as the legacy sqlite3-based
    helper, but every query runs against the shared Postgres database
    with table names auto-rewritten to their `mrc_` prefix.
    """
    eng = get_engine()
    if SessionLocal.kw.get("bind") is None:
        SessionLocal.configure(bind=eng)
    sa_conn = eng.connect()
    inner = PgConnectionFixed(sa_conn)
    wrapper = _MarcelConnection(inner)
    try:
        yield wrapper
    finally:
        wrapper.close()


def q_one(sql: str, *params: Any):
    """Run `sql` and return the first column of the first row, or 0 if empty.

    Mirrors legacy semantics — most call sites use it as a COUNT() helper.
    """
    with conn() as c:
        r = c.execute(sql, params).fetchone()
        return r[0] if r else 0


def q_all(sql: str, *params: Any) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
