"""
LinkedIn source — SQLite schema + helpers.

This DB is an isolated sibling of the Marcel and grab-source DBs. It is created
on first access; schema lives here (not in a migration folder) because there
is exactly one active version and fresh-start is a product decision.

Public surface:
    DB_PATH            — absolute path to leads.db
    connect()          — context-managed connection with row_factory set
    init()             — idempotent schema bootstrap
    ensure_safety_row()— seed the singleton safety_state row
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

BASE = Path(r"H:/Lead Generator/B2B")
DB_PATH = BASE / "Database" / "LinkedIn Data" / "leads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  post_url        TEXT UNIQUE NOT NULL,
  posted_by       TEXT,
  company         TEXT,
  role            TEXT,
  tech_stack      TEXT,
  rate            TEXT,
  location        TEXT,
  tags            TEXT,
  post_text       TEXT,
  email           TEXT,
  phone           TEXT,
  status          TEXT NOT NULL DEFAULT 'New',
  gen_subject     TEXT,
  gen_body        TEXT,
  email_mode      TEXT NOT NULL DEFAULT 'company',
  cv_cluster      TEXT,
  jaydip_note     TEXT,
  skip_reason     TEXT,
  skip_source     TEXT,
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  queued_at       TEXT,
  sent_at         TEXT,
  replied_at      TEXT,
  bounced_at      TEXT,
  follow_up_at    TEXT,
  needs_attention INTEGER NOT NULL DEFAULT 0,
  sent_message_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_status    ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_attention ON leads(needs_attention);
CREATE INDEX IF NOT EXISTS idx_leads_last_seen ON leads(last_seen_at);

CREATE TABLE IF NOT EXISTS recyclebin (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  original_id  INTEGER,
  post_url     TEXT UNIQUE,
  payload_json TEXT NOT NULL,
  reason       TEXT NOT NULL,
  moved_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS safety_state (
  id                   INTEGER PRIMARY KEY CHECK (id = 1),
  daily_sent_count     INTEGER NOT NULL DEFAULT 0,
  daily_sent_date      TEXT,
  last_send_at         TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  warning_paused_until TEXT,
  autopilot_enabled    INTEGER NOT NULL DEFAULT 0,
  autopilot_hour       INTEGER NOT NULL DEFAULT 10,
  safety_mode          TEXT NOT NULL DEFAULT 'max'
);

CREATE TABLE IF NOT EXISTS replies (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id         INTEGER NOT NULL REFERENCES leads(id),
  gmail_msg_id    TEXT UNIQUE NOT NULL,
  gmail_thread_id TEXT,
  from_email      TEXT,
  subject         TEXT,
  snippet         TEXT,
  received_at     TEXT NOT NULL,
  kind            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_replies_lead ON replies(lead_id);

CREATE TABLE IF NOT EXISTS gmail_auth (
  id               INTEGER PRIMARY KEY CHECK (id = 1),
  email            TEXT,
  app_password_enc TEXT,                  -- Fernet-encrypted app password
  connected_at     TEXT,
  last_verified_at TEXT,
  imap_uid_seen    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS extension_keys (
  key          TEXT PRIMARY KEY,
  label        TEXT,
  created_at   TEXT NOT NULL,
  last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  kind      TEXT NOT NULL,
  lead_id   INTEGER,
  meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);

-- Blocklist: suppress ingest + send for matching companies / domains.
-- Reason is free-text for audit ("upwork contract noise", "past bad fit").
CREATE TABLE IF NOT EXISTS blocklist (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,              -- company | domain
  value      TEXT NOT NULL,              -- lowercase normalized
  reason     TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(kind, value)
);

-- CV library: uploaded PDFs auto-picked by cv_cluster at send time.
CREATE TABLE IF NOT EXISTS cvs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  cluster     TEXT NOT NULL UNIQUE,       -- python_ai | fullstack | scraping | n8n | default
  filename    TEXT NOT NULL,              -- original upload filename
  stored_path TEXT NOT NULL,              -- absolute path to file on disk
  size_bytes  INTEGER,
  uploaded_at TEXT NOT NULL
);

-- Follow-ups: tracks which leads have been followed up and when.
-- First follow-up fires 3 days after last_sent if no reply, second 7 days after first.
CREATE TABLE IF NOT EXISTS followups (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id       INTEGER NOT NULL REFERENCES leads(id),
  sequence      INTEGER NOT NULL,          -- 1 = first follow-up, 2 = second
  message_id    TEXT,
  sent_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_followups_lead ON followups(lead_id);

-- Autopilot state (tracked per-day for visibility).
CREATE TABLE IF NOT EXISTS autopilot_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  fired_at      TEXT NOT NULL,
  fired_date    TEXT NOT NULL UNIQUE,
  total_queued  INTEGER,
  status        TEXT NOT NULL              -- started | skipped_quiet | skipped_quota | skipped_paused | skipped_no_drafts
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Yield a connection with row_factory=Row. Caller commits explicitly."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
    finally:
        con.close()


def init() -> None:
    """Create tables + seed the singleton safety row. Idempotent."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        _migrate(con)
        ensure_safety_row(con)
        con.commit()


def _migrate(con) -> None:
    """Add columns introduced after initial schema. Idempotent."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(leads)").fetchall()}
    if "sent_message_id" not in cols:
        con.execute("ALTER TABLE leads ADD COLUMN sent_message_id TEXT")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_msgid ON leads(sent_message_id)"
    )


def ensure_safety_row(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT 1 FROM safety_state WHERE id = 1").fetchone()
    if row is None:
        con.execute(
            "INSERT INTO safety_state (id, daily_sent_date) VALUES (1, ?)",
            (dt.date.today().isoformat(),),
        )


if __name__ == "__main__":
    init()
    print(f"[ok] initialised {DB_PATH}")
