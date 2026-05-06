"""
LinkedIn source — SQLAlchemy models + helpers (PostgreSQL).

Public surface:
    connect()          — context-managed connection with dict-row access
    get_engine()       — lazily-created SQLAlchemy engine
    SessionLocal       — scoped session factory
    Base               — declarative base (all models inherit from this)
    init()             — idempotent schema bootstrap
    ensure_safety_row()— seed the singleton safety_state row

Filesystem locations for ancillary files now live with their consumers:
    CV PDFs        → app/linkedin/extras.py: CV_STORAGE_DIR (app/static/cvs/)
    Fernet key     → app/linkedin/services/gmail.py: _KEY_FILE (app/.fernet.key)
"""
from __future__ import annotations

import datetime as dt
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# ---------------------------------------------------------------------------
# SQLAlchemy setup
# ---------------------------------------------------------------------------
Base = declarative_base()

_engine = None


def get_engine():
    """Lazily create the SQLAlchemy engine.

    Reads DATABASE_URL from the environment at call time (not import time)
    so that dotenv / startup scripts can set it before the first connection.
    """
    global _engine
    if _engine is None:
        # Load .env if present — idempotent, won't override existing env.
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        db_url = os.environ.get(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/linkedin_leads",
        )
        _engine = create_engine(
            db_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


SessionLocal = sessionmaker(bind=None)  # bind set in init()


# ---------------------------------------------------------------------------
# PgConnection adapter
#
# Wraps a SQLAlchemy Connection to expose an API compatible with the
# sqlite3.Connection interface used by 76+ call sites across the codebase.
#
# Key translations:
#   - ? placeholders  →  %s  (psycopg2 paramstyle)
#   - INSERT OR REPLACE INTO <t> (...) VALUES (...)
#         → INSERT INTO <t> (...) VALUES (...) ON CONFLICT (<pk>) DO UPDATE SET ...
#   - INSERT OR IGNORE INTO <t> (...) VALUES (...)
#         → INSERT INTO <t> (...) VALUES (...) ON CONFLICT DO NOTHING
#   - row["col"] access via DictRow wrapper
#   - .lastrowid via RETURNING id (auto-appended on INSERT)
#   - .rowcount on CursorResult
# ---------------------------------------------------------------------------

# Map table name → conflict target columns for INSERT OR REPLACE rewriting.
# Only tables that actually use INSERT OR REPLACE/IGNORE need entries here.
_CONFLICT_KEYS: dict[str, list[str]] = {
    "ln_recyclebin": ["post_url"],
    "ln_archived_urls": ["post_url"],
    "ln_autopilot_runs": ["fired_date"],
    "ln_replies": ["gmail_msg_id"],
    "ln_kv_settings": ["key"],
    "ln_company_enrichment": ["company"],
    "ln_blocklist": ["kind", "value"],
}

# INSERT OR REPLACE pattern
_RE_INSERT_OR_REPLACE = re.compile(
    r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
    re.IGNORECASE,
)
# INSERT OR IGNORE pattern
_RE_INSERT_OR_IGNORE = re.compile(
    r"INSERT\s+OR\s+IGNORE\s+INTO\s+(\w+)",
    re.IGNORECASE,
)


class DictRow:
    """Mimics sqlite3.Row: supports row["col"], row[0], dict(row), keys()."""

    __slots__ = ("_data", "_keys")

    def __init__(self, mapping):
        self._keys = list(mapping.keys())
        self._data = dict(mapping)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._data[self._keys[key]]
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return self._keys

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __iter__(self):
        return iter(self._data.values())

    def __repr__(self):
        return f"DictRow({self._data})"


class CursorResult:
    """Thin wrapper around SQLAlchemy CursorResult that exposes .lastrowid,
    .rowcount, .fetchone(), .fetchall() with DictRow results."""

    __slots__ = ("_result", "lastrowid", "rowcount")

    def __init__(self, result, lastrowid=None):
        self._result = result
        self.rowcount = result.rowcount if result.rowcount >= 0 else 0
        self.lastrowid = lastrowid

    def fetchone(self):
        row = self._result.fetchone()
        if row is None:
            return None
        return DictRow(row._mapping)

    def fetchall(self):
        rows = self._result.fetchall()
        return [DictRow(r._mapping) for r in rows]


def _rewrite_sql(sql: str) -> str:
    """Convert SQLite-isms to PostgreSQL-compatible SQL."""
    # 1) INSERT OR REPLACE → INSERT ... ON CONFLICT (...) DO UPDATE SET ...
    m = _RE_INSERT_OR_REPLACE.search(sql)
    if m:
        table = m.group(1)
        cols_str = m.group(2)
        vals_str = m.group(3)
        cols = [c.strip() for c in cols_str.split(",")]
        conflict_cols = _CONFLICT_KEYS.get(table, [])
        if conflict_cols:
            update_cols = [c for c in cols if c not in conflict_cols]
            set_clause = ", ".join(
                f"{c} = excluded.{c}" for c in update_cols
            )
            sql = (
                f"INSERT INTO {table} ({cols_str}) VALUES ({vals_str}) "
                f"ON CONFLICT ({', '.join(conflict_cols)}) "
                f"DO UPDATE SET {set_clause}"
            )
        else:
            # No known conflict key — just do a plain insert
            sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO", 1)

    # 2) INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    m2 = _RE_INSERT_OR_IGNORE.search(sql)
    if m2:
        table = m2.group(1)
        conflict_cols = _CONFLICT_KEYS.get(table)
        if conflict_cols:
            sql = re.sub(
                r"INSERT\s+OR\s+IGNORE\s+INTO",
                "INSERT INTO",
                sql,
                count=1,
                flags=re.IGNORECASE,
            )
            # Find the VALUES (...) and append ON CONFLICT
            # Check if there's already an ON CONFLICT
            if "ON CONFLICT" not in sql.upper():
                sql += f" ON CONFLICT ({', '.join(conflict_cols)}) DO NOTHING"
        else:
            sql = re.sub(
                r"INSERT\s+OR\s+IGNORE\s+INTO",
                "INSERT INTO",
                sql,
                count=1,
                flags=re.IGNORECASE,
            )
            if "ON CONFLICT" not in sql.upper():
                sql += " ON CONFLICT DO NOTHING"

    # 3) DATE('now', '-N day/days') → CURRENT_DATE - INTERVAL 'N days'
    sql = re.sub(
        r"DATE\('now',\s*'-(\d+)\s*days?'\)",
        r"(CURRENT_DATE - INTERVAL '\1 days')",
        sql,
        flags=re.IGNORECASE,
    )
    # DATE('now') → CURRENT_DATE
    sql = re.sub(r"DATE\('now'\)", "CURRENT_DATE", sql, flags=re.IGNORECASE)

    # 4) ? → %s placeholders
    sql = sql.replace("?", "%s")

    return sql


class PgConnection:
    """Drop-in replacement for sqlite3.Connection. Wraps a SQLAlchemy
    Connection and translates SQLite-style calls to PostgreSQL."""

    def __init__(self, sa_conn):
        self._conn = sa_conn

    def execute(self, sql: str, params: Sequence = ()) -> CursorResult:
        rewritten = _rewrite_sql(sql)

        # Auto-append RETURNING id on INSERT to capture lastrowid
        lastrowid = None
        is_insert = rewritten.lstrip().upper().startswith("INSERT")

        if is_insert and "RETURNING" not in rewritten.upper():
            rewritten_with_returning = rewritten + " RETURNING id"
        else:
            rewritten_with_returning = None

        try:
            if rewritten_with_returning and is_insert:
                result = self._conn.execute(
                    text(rewritten_with_returning), _params_to_dict(rewritten_with_returning, params)
                )
                row = result.fetchone()
                if row:
                    lastrowid = row[0]
                    # Reconstruct result for the caller
                    result = self._conn.execute(
                        text("SELECT 1 WHERE false")
                    )
                return CursorResult(result, lastrowid=lastrowid)
            else:
                result = self._conn.execute(
                    text(rewritten), _params_to_dict(rewritten, params)
                )
                return CursorResult(result)
        except Exception:
            # If RETURNING fails (e.g. table has no 'id' column), retry without
            if rewritten_with_returning and is_insert:
                result = self._conn.execute(
                    text(rewritten), _params_to_dict(rewritten, params)
                )
                return CursorResult(result)
            raise

    def executemany(self, sql: str, params_list: Sequence[Sequence]) -> None:
        rewritten = _rewrite_sql(sql)
        for params in params_list:
            self._conn.execute(
                text(rewritten), _params_to_dict(rewritten, params)
            )

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def _params_to_dict(sql: str, params: Sequence) -> dict:
    """Convert positional %s params to named :p0, :p1, ... params.
    SQLAlchemy text() requires named params."""
    if not params:
        return {}
    # Replace %s with :p0, :p1, ... and build the dict
    result_sql_parts = []
    idx = 0
    mapping = {}
    i = 0
    while i < len(sql):
        if sql[i] == '%' and i + 1 < len(sql) and sql[i + 1] == 's':
            key = f"p{idx}"
            mapping[key] = params[idx] if idx < len(params) else None
            idx += 1
            i += 2
        else:
            i += 1
    return mapping


def _rewrite_sql_named(sql: str, params: Sequence) -> tuple[str, dict]:
    """Rewrite %s placeholders to :p0, :p1, ... and return (sql, params_dict)."""
    mapping = {}
    idx = 0
    new_sql = []
    i = 0
    while i < len(sql):
        if sql[i] == '%' and i + 1 < len(sql) and sql[i + 1] == 's':
            key = f"p{idx}"
            mapping[key] = params[idx] if idx < len(params) else None
            new_sql.append(f":{key}")
            idx += 1
            i += 2
        else:
            new_sql.append(sql[i])
            i += 1
    return "".join(new_sql), mapping


# Tables whose primary key is named 'id' — only these get RETURNING id.
_TABLES_WITH_ID_PK = {
    "leads", "recyclebin", "safety_state", "replies", "gmail_accounts",
    "extension_keys", "events", "blocklist", "cvs", "followups",
    "autopilot_runs", "email_opens",
}

# Regex to extract table name from INSERT INTO <table>
_RE_INSERT_TABLE = re.compile(
    r"INSERT\s+INTO\s+(\w+)", re.IGNORECASE
)


class PgConnectionFixed(PgConnection):
    """Improved PgConnection that properly handles named parameters."""

    def execute(self, sql: str, params: Sequence = ()) -> CursorResult:
        rewritten = _rewrite_sql(sql)

        # Convert %s → :p0, :p1, ... for SQLAlchemy text()
        named_sql, param_dict = _rewrite_sql_named(rewritten, params)

        # Auto-append RETURNING id on INSERT to capture lastrowid,
        # but only for tables that actually have an 'id' column.
        lastrowid = None
        is_insert = named_sql.lstrip().upper().startswith("INSERT")

        should_return_id = False
        if is_insert and "RETURNING" not in named_sql.upper():
            m = _RE_INSERT_TABLE.search(named_sql)
            if m and m.group(1).lower() in _TABLES_WITH_ID_PK:
                should_return_id = True

        if should_return_id:
            result = self._conn.execute(
                text(named_sql + " RETURNING id"), param_dict
            )
            row = result.fetchone()
            if row:
                lastrowid = row[0]
            # Return a "spent" result — the INSERT already consumed its rows
            class _SpentResult:
                def __init__(self, rr):
                    self.rowcount = 1 if lastrowid else (rr.rowcount if rr.rowcount >= 0 else 0)
                def fetchone(self): return None
                def fetchall(self): return []
            return CursorResult(_SpentResult(result), lastrowid=lastrowid)
        else:
            result = self._conn.execute(text(named_sql), param_dict)
            return CursorResult(result)

    def executemany(self, sql: str, params_list: Sequence[Sequence]) -> None:
        rewritten = _rewrite_sql(sql)
        for params in params_list:
            named_sql, param_dict = _rewrite_sql_named(rewritten, params)
            self._conn.execute(text(named_sql), param_dict)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Lead(Base):
    __tablename__ = "ln_leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_url = Column(Text, unique=True, nullable=False)
    posted_by = Column(Text)
    company = Column(Text)
    role = Column(Text)
    tech_stack = Column(Text)
    rate = Column(Text)
    location = Column(Text)
    tags = Column(Text)
    post_text = Column(Text)
    email = Column(Text)
    phone = Column(Text)
    status = Column(Text, nullable=False, server_default="New")
    gen_subject = Column(Text)
    gen_body = Column(Text)
    email_mode = Column(Text, nullable=False, server_default="company")
    cv_cluster = Column(Text)
    jaydip_note = Column(Text)
    skip_reason = Column(Text)
    skip_source = Column(Text)
    first_seen_at = Column(Text, nullable=False)
    last_seen_at = Column(Text, nullable=False)
    queued_at = Column(Text)
    sent_at = Column(Text)
    replied_at = Column(Text)
    bounced_at = Column(Text)
    follow_up_at = Column(Text)
    needs_attention = Column(Integer, nullable=False, server_default="0")
    sent_message_id = Column(Text)
    # ISO timestamp the lead is snoozed until. While set + in the future
    # needs_attention is forced to 0 and the leads list dims the row.
    remind_at = Column(Text)

    # --- columns added via migration (now part of canonical schema) ---
    sent_via_account_id = Column(Integer)
    call_status = Column(Text)
    reviewed_at = Column(Text)
    open_token = Column(Text)
    open_count = Column(Integer, nullable=False, server_default="0")
    first_opened_at = Column(Text)
    last_opened_at = Column(Text)
    scheduled_send_at = Column(Text)
    ooo_nudge_at = Column(Text)
    ooo_nudge_sent_at = Column(Text)
    fit_score = Column(Integer)
    fit_score_reasons = Column(Text)

    # relationships
    replies = relationship("Reply", back_populates="lead")
    followups = relationship("Followup", back_populates="lead")
    email_opens = relationship("EmailOpen", back_populates="lead")

    __table_args__ = (
        Index("idx_leads_status", "status"),
        Index("idx_leads_attention", "needs_attention"),
        Index("idx_leads_last_seen", "last_seen_at"),
        Index("idx_leads_remind_at", "remind_at"),
        Index(
            "idx_leads_scheduled",
            "scheduled_send_at",
            postgresql_where=text("scheduled_send_at IS NOT NULL"),
        ),
        Index("idx_leads_msgid", "sent_message_id"),
        Index("idx_leads_open_token", "open_token"),
    )


class RecycleBin(Base):
    __tablename__ = "ln_recyclebin"

    id = Column(Integer, primary_key=True, autoincrement=True)
    original_id = Column(Integer)
    post_url = Column(Text, unique=True)
    payload_json = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    moved_at = Column(Text, nullable=False)


class ArchivedUrl(Base):
    """Permanent dedup shadow."""

    __tablename__ = "ln_archived_urls"

    post_url = Column(Text, primary_key=True)
    reason = Column(Text)
    archived_at = Column(Text, nullable=False)


class SafetyState(Base):
    __tablename__ = "ln_safety_state"

    id = Column(Integer, primary_key=True)
    daily_sent_count = Column(Integer, nullable=False, server_default="0")
    daily_sent_date = Column(Text)
    last_send_at = Column(Text)
    consecutive_failures = Column(Integer, nullable=False, server_default="0")
    warning_paused_until = Column(Text)
    autopilot_enabled = Column(Integer, nullable=False, server_default="0")
    autopilot_hour = Column(Integer, nullable=False, server_default="10")
    autopilot_minute = Column(Integer, nullable=False, server_default="0")
    autopilot_count = Column(Integer)
    autopilot_tz = Column(Text, nullable=False, server_default="")
    business_hours_only = Column(Integer, nullable=False, server_default="0")
    safety_mode = Column(Text, nullable=False, server_default="max")
    warmup_curve_json = Column(Text)
    followups_autopilot = Column(Integer, nullable=False, server_default="0")
    followups_hour = Column(Integer, nullable=False, server_default="11")

    __table_args__ = (
        CheckConstraint("id = 1", name="singleton_safety_state"),
    )


class Reply(Base):
    __tablename__ = "ln_replies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("ln_leads.id"), nullable=False)
    gmail_msg_id = Column(Text, unique=True, nullable=False)
    gmail_thread_id = Column(Text)
    from_email = Column(Text)
    subject = Column(Text)
    snippet = Column(Text)
    received_at = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    handled_at = Column(Text)
    sentiment = Column(Text)
    body = Column(Text)
    auto_draft_body = Column(Text)
    auto_draft_at = Column(Text)
    intent = Column(Text)

    lead = relationship("Lead", back_populates="replies")

    __table_args__ = (
        Index("idx_replies_lead", "lead_id"),
    )


class GmailAccount(Base):
    """Multi-account Gmail rotation."""

    __tablename__ = "ln_gmail_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False, unique=True)
    app_password_enc = Column(Text, nullable=False)
    display_name = Column(Text)
    daily_cap = Column(Integer, nullable=False, server_default="50")
    sent_today = Column(Integer, nullable=False, server_default="0")
    sent_date = Column(Text)
    last_sent_at = Column(Text)
    imap_uid_seen = Column(Integer, nullable=False, server_default="0")
    status = Column(Text, nullable=False, server_default="active")
    warmup_enabled = Column(Integer, nullable=False, server_default="1")
    warmup_start_date = Column(Text)
    connected_at = Column(Text, nullable=False)
    last_verified_at = Column(Text)
    consecutive_failures = Column(Integer, nullable=False, server_default="0")
    bounce_count_today = Column(Integer, nullable=False, server_default="0")
    paused_reason = Column(Text)

    __table_args__ = (
        Index("idx_gmail_accounts_status", "status"),
    )


class ExtensionKey(Base):
    __tablename__ = "ln_extension_keys"

    key = Column(Text, primary_key=True)
    label = Column(Text)
    created_at = Column(Text, nullable=False)
    last_used_at = Column(Text)


class Event(Base):
    __tablename__ = "ln_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    at = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    lead_id = Column(Integer)
    meta_json = Column(Text)

    __table_args__ = (
        Index("idx_events_at", "at"),
    )


class BlocklistEntry(Base):
    """Blocklist: suppress ingest + send for matching companies / domains."""

    __tablename__ = "ln_blocklist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(Text, nullable=False)
    value = Column(Text, nullable=False)
    reason = Column(Text)
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("kind", "value", name="uq_blocklist_kind_value"),
    )


class CV(Base):
    """CV library: uploaded PDFs auto-picked by cv_cluster at send time."""

    __tablename__ = "ln_cvs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cluster = Column(Text, nullable=False, unique=True)
    filename = Column(Text, nullable=False)
    stored_path = Column(Text, nullable=False)
    size_bytes = Column(Integer)
    uploaded_at = Column(Text, nullable=False)


class Followup(Base):
    """Follow-ups: tracks which leads have been followed up and when."""

    __tablename__ = "ln_followups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("ln_leads.id"), nullable=False)
    sequence = Column(Integer, nullable=False)
    message_id = Column(Text)
    sent_at = Column(Text, nullable=False)

    lead = relationship("Lead", back_populates="followups")

    __table_args__ = (
        Index("idx_followups_lead", "lead_id"),
    )


class CompanyEnrichment(Base):
    """Per-company enrichment cache."""

    __tablename__ = "ln_company_enrichment"

    company = Column(Text, primary_key=True)
    summary = Column(Text)
    source = Column(Text)
    fetched_at = Column(Text, nullable=False)


class AutopilotRun(Base):
    """Autopilot state (tracked per-day for visibility)."""

    __tablename__ = "ln_autopilot_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fired_at = Column(Text, nullable=False)
    fired_date = Column(Text, nullable=False, unique=True)
    total_queued = Column(Integer)
    status = Column(Text, nullable=False)


class KVSetting(Base):
    """Generic key/value runtime settings."""

    __tablename__ = "ln_kv_settings"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)


class EmailOpen(Base):
    """Individual open events (one row per open — pixel fetch)."""

    __tablename__ = "ln_email_opens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("ln_leads.id"), nullable=False)
    opened_at = Column(Text, nullable=False)
    user_agent = Column(Text)
    ip = Column(Text)

    lead = relationship("Lead", back_populates="email_opens")

    __table_args__ = (
        Index("idx_email_opens_lead", "lead_id"),
    )


# ---------------------------------------------------------------------------
# Public connection helper — drop-in for the old sqlite3-based connect()
# ---------------------------------------------------------------------------

@contextmanager
def connect() -> Iterator[PgConnectionFixed]:
    """Yield a PgConnection wrapping a SQLAlchemy connection.

    Usage is identical to the old sqlite3 pattern::

        with connect() as con:
            row = con.execute("SELECT * FROM ln_leads WHERE id = ?", (42,)).fetchone()
            print(row["company"])
            con.commit()
    """
    engine = get_engine()
    raw = engine.connect()
    con = PgConnectionFixed(raw)
    try:
        yield con
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def init() -> None:
    """Run Alembic migrations to head + seed the singleton safety row.

    Replaces the old ``Base.metadata.create_all()`` approach so that
    all schema changes are tracked by Alembic migration scripts.
    Idempotent — safe to call on every app boot.
    """
    eng = get_engine()
    SessionLocal.configure(bind=eng)

    # Run Alembic migrations programmatically
    from alembic.config import Config as AlembicConfig
    from alembic import command as alembic_command

    # alembic.ini + migrations/ live at the backend root; this file is at
    # backend/app/linkedin/db.py, so step up two levels.
    _BACKEND_ROOT = Path(__file__).resolve().parents[2]
    alembic_cfg = AlembicConfig(
        str(_BACKEND_ROOT / "alembic.ini")
    )
    # Tell migrations/env.py to skip its `fileConfig(...)` call. Otherwise
    # alembic.ini's logger config (root=WARNING + disable_existing_loggers=True)
    # silently clobbers uvicorn's `uvicorn.access` logger and per-request
    # access lines stop reaching the console.
    alembic_cfg.attributes["skip_logging"] = True
    # Override the script_location to be absolute so it works regardless
    # of the cwd when the app is launched.
    alembic_cfg.set_main_option(
        "script_location",
        str(_BACKEND_ROOT / "migrations"),
    )
    # Pass the engine's URL so Alembic doesn't need to re-read .env
    alembic_cfg.set_main_option(
        "sqlalchemy.url",
        str(eng.url),
    )
    alembic_command.upgrade(alembic_cfg, "head")

    with connect() as con:
        ensure_safety_row(con)
        con.commit()


# --- kv_settings helpers ---------------------------------------------------


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def get_setting_raw(key: str) -> Optional[str]:
    """Return the raw stored string for `key`, or None if not set."""
    with connect() as con:
        r = con.execute(
            "SELECT value FROM ln_kv_settings WHERE key = ?", (key,),
        ).fetchone()
    return r["value"] if r else None


def set_setting_raw(key: str, value: str) -> None:
    """Upsert a raw string value."""
    with connect() as con:
        con.execute(
            "INSERT INTO ln_kv_settings (key, value, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "  updated_at = excluded.updated_at",
            (key, value, _now_iso()),
        )
        con.commit()


def get_setting_bool(key: str, env_key: Optional[str] = None,
                     default: bool = False) -> bool:
    """Bool setting with env fallback."""
    raw = get_setting_raw(key)
    if raw is None and env_key:
        raw = os.environ.get(env_key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_setting_int(key: str, env_key: Optional[str] = None,
                    default: int = 0) -> int:
    raw = get_setting_raw(key)
    if raw is None and env_key:
        raw = os.environ.get(env_key)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def ensure_safety_row(con) -> None:
    row = con.execute("SELECT 1 FROM ln_safety_state WHERE id = 1").fetchone()
    if row is None:
        con.execute(
            "INSERT INTO ln_safety_state (id, daily_sent_date) VALUES (?, ?)",
            (1, dt.date.today().isoformat()),
        )


if __name__ == "__main__":
    init()
    print(f"[ok] initialised PostgreSQL database")
