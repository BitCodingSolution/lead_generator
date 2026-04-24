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

-- Multi-account Gmail rotation. Each row = one connected Gmail with its
-- own app password, daily cap, and IMAP cursor. Picker does round-robin
-- across active rows so total per-day throughput scales with account count.
CREATE TABLE IF NOT EXISTS gmail_accounts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  email             TEXT NOT NULL UNIQUE,
  app_password_enc  TEXT NOT NULL,
  display_name      TEXT,
  daily_cap         INTEGER NOT NULL DEFAULT 50,
  sent_today        INTEGER NOT NULL DEFAULT 0,
  sent_date         TEXT,                      -- YYYY-MM-DD; reset when day rolls over
  last_sent_at      TEXT,
  imap_uid_seen     INTEGER NOT NULL DEFAULT 0,
  status            TEXT NOT NULL DEFAULT 'active',  -- active | paused
  warmup_enabled    INTEGER NOT NULL DEFAULT 1,      -- ramp cap up over first 14 days
  warmup_start_date TEXT,                            -- YYYY-MM-DD; NULL → use connected_at
  connected_at      TEXT NOT NULL,
  last_verified_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_gmail_accounts_status ON gmail_accounts(status);

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
    if "sent_via_account_id" not in cols:
        con.execute("ALTER TABLE leads ADD COLUMN sent_via_account_id INTEGER")
    if "call_status" not in cols:
        con.execute("ALTER TABLE leads ADD COLUMN call_status TEXT")
    if "reviewed_at" not in cols:
        con.execute("ALTER TABLE leads ADD COLUMN reviewed_at TEXT")
    if "open_token" not in cols:
        con.execute("ALTER TABLE leads ADD COLUMN open_token TEXT")
    if "open_count" not in cols:
        con.execute(
            "ALTER TABLE leads ADD COLUMN open_count INTEGER NOT NULL DEFAULT 0"
        )
    if "first_opened_at" not in cols:
        con.execute("ALTER TABLE leads ADD COLUMN first_opened_at TEXT")
    if "last_opened_at" not in cols:
        con.execute("ALTER TABLE leads ADD COLUMN last_opened_at TEXT")
    if "scheduled_send_at" not in cols:
        # ISO-8601 timestamp. When set on a Drafted lead, a background
        # scheduler picks it up at/after this time and sends via the
        # standard send_one path (safety, warmup, blocklist all apply).
        con.execute("ALTER TABLE leads ADD COLUMN scheduled_send_at TEXT")
    if "ooo_nudge_at" not in cols:
        # When an inbound OOO is detected, scheduler auto-stamps this to
        # ~7 days from today (9am local). Separate from scheduled_send_at
        # because the nudge is a THREAD reply, not a fresh send.
        con.execute("ALTER TABLE leads ADD COLUMN ooo_nudge_at TEXT")
    if "ooo_nudge_sent_at" not in cols:
        # Stamp when the nudge actually goes out - prevents double-nudge.
        con.execute("ALTER TABLE leads ADD COLUMN ooo_nudge_sent_at TEXT")
    if "fit_score" not in cols:
        # 0-100 heuristic priority score. Computed at ingest and any time
        # key fields change (email set, draft generated, etc.).
        con.execute("ALTER TABLE leads ADD COLUMN fit_score INTEGER")
    if "fit_score_reasons" not in cols:
        # JSON array of short reason strings - UI can surface why a lead
        # scored what it scored.
        con.execute("ALTER TABLE leads ADD COLUMN fit_score_reasons TEXT")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_scheduled "
        "ON leads(scheduled_send_at) WHERE scheduled_send_at IS NOT NULL"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_msgid ON leads(sent_message_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_open_token ON leads(open_token)"
    )
    # Individual open events (one row per open — pixel fetch). Useful for
    # spotting repeat engagement vs. a single Gmail proxy prefetch.
    con.execute("""
        CREATE TABLE IF NOT EXISTS email_opens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id     INTEGER NOT NULL REFERENCES leads(id),
            opened_at   TEXT NOT NULL,
            user_agent  TEXT,
            ip          TEXT
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_opens_lead ON email_opens(lead_id)"
    )
    rep_cols = {r[1] for r in con.execute("PRAGMA table_info(replies)").fetchall()}
    if rep_cols:
        # Existing replies table (LinkedIn/campaigns flow). Extend with
        # handled_at + sentiment so we can triage from the dashboard.
        if "handled_at" not in rep_cols:
            con.execute("ALTER TABLE replies ADD COLUMN handled_at TEXT")
        if "sentiment" not in rep_cols:
            con.execute("ALTER TABLE replies ADD COLUMN sentiment TEXT")
        if "body" not in rep_cols:
            # Full body — snippet is only first 500 chars, not enough for
            # drafting a response.
            con.execute("ALTER TABLE replies ADD COLUMN body TEXT")
        if "auto_draft_body" not in rep_cols:
            # Background-generated suggested reply (via Bridge) populated
            # right after IMAP poll. UI pre-fills the textarea with this.
            con.execute("ALTER TABLE replies ADD COLUMN auto_draft_body TEXT")
        if "auto_draft_at" not in rep_cols:
            con.execute("ALTER TABLE replies ADD COLUMN auto_draft_at TEXT")

    acct_cols = {
        r[1] for r in con.execute("PRAGMA table_info(gmail_accounts)").fetchall()
    }
    if acct_cols and "warmup_enabled" not in acct_cols:
        con.execute(
            "ALTER TABLE gmail_accounts ADD COLUMN warmup_enabled INTEGER "
            "NOT NULL DEFAULT 1"
        )
    if acct_cols and "warmup_start_date" not in acct_cols:
        con.execute(
            "ALTER TABLE gmail_accounts ADD COLUMN warmup_start_date TEXT"
        )
    if acct_cols and "consecutive_failures" not in acct_cols:
        con.execute(
            "ALTER TABLE gmail_accounts ADD COLUMN consecutive_failures "
            "INTEGER NOT NULL DEFAULT 0"
        )
    if acct_cols and "bounce_count_today" not in acct_cols:
        con.execute(
            "ALTER TABLE gmail_accounts ADD COLUMN bounce_count_today "
            "INTEGER NOT NULL DEFAULT 0"
        )
    if acct_cols and "paused_reason" not in acct_cols:
        con.execute(
            "ALTER TABLE gmail_accounts ADD COLUMN paused_reason TEXT"
        )

    # Global warmup curve — stored as JSON on safety_state so users can
    # tune the ramp to match their deliverability tolerance without a
    # schema change.
    safety_cols = {
        r[1] for r in con.execute("PRAGMA table_info(safety_state)").fetchall()
    }
    if "warmup_curve_json" not in safety_cols:
        con.execute(
            "ALTER TABLE safety_state ADD COLUMN warmup_curve_json TEXT"
        )

    # One-shot migration: if a pre-multi-account gmail_auth singleton exists
    # (old installs) and gmail_accounts is empty, seed the first account from
    # it so nothing breaks on upgrade. Fresh installs skip this entirely —
    # the table may not exist.
    legacy_exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gmail_auth'"
    ).fetchone()
    if legacy_exists:
        has_legacy = con.execute(
            "SELECT email, app_password_enc, connected_at, last_verified_at, "
            "       imap_uid_seen FROM gmail_auth WHERE id = 1"
        ).fetchone()
        new_count = con.execute(
            "SELECT COUNT(*) FROM gmail_accounts"
        ).fetchone()[0]
        if has_legacy and has_legacy["email"] and has_legacy["app_password_enc"] and new_count == 0:
            # Legacy accounts are already warm — don't retroactively throttle them.
            con.execute(
                "INSERT INTO gmail_accounts (email, app_password_enc, "
                "connected_at, last_verified_at, imap_uid_seen, status, "
                "warmup_enabled) VALUES (?, ?, ?, ?, ?, 'active', 0)",
                (
                    has_legacy["email"],
                    has_legacy["app_password_enc"],
                    has_legacy["connected_at"] or dt.datetime.now().isoformat(timespec="seconds"),
                    has_legacy["last_verified_at"],
                    int(has_legacy["imap_uid_seen"] or 0),
                ),
            )
        # Table served its purpose — drop it so future audits don't flag it.
        con.execute("DROP TABLE IF EXISTS gmail_auth")

    # Any pre-existing account row that already has historical sends should
    # also be treated as warm — otherwise turning on warmup late would hard-
    # cap an account that's been sending 20+/day safely for weeks.
    con.execute(
        "UPDATE gmail_accounts SET warmup_enabled = 0 "
        "WHERE warmup_enabled = 1 AND EXISTS ("
        "  SELECT 1 FROM leads WHERE sent_via_account_id = gmail_accounts.id "
        ")"
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
