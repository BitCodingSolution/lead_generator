"""
One-shot importer: merge two SQLite leads DBs into Postgres.

Reads the live DB and the 2026-05-02 backup, dedupes via natural keys
(or full-row content hash for keyless tables), and bulk-loads into the
Postgres database pointed to by DATABASE_URL.

Live wins on conflicts; backup-only rows are appended. Postgres tables
must already exist (created by Alembic). The script truncates each
target table inside a single transaction before re-loading.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Sequence

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


LIVE_DB = Path(
    "/Users/jaydip/Documents/BCS/admin/lead_generator/B2B/Database/LinkedIn Data/leads.db"
)
BACKUP_DB = Path(
    "/Users/jaydip/Documents/BCS/admin/lead_generator/B2B/Database/Backups/leads-2026-05-02.db"
)


# Per-table merge spec. Order matters for foreign keys: parents first.
# - cols: explicit column list (matches both SQLite and Postgres schemas)
# - key:  natural-key columns for dedupe across sources, or None to dedupe
#         via the full row content
# - has_id: True if Postgres has an autoincrement `id` sequence to reset
TABLES: list[dict] = [
    # Standalone reference tables
    {"name": "archived_urls",      "cols": ["post_url", "reason", "archived_at"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  "key": ("post_url",),       "has_id": False},
    {"name": "autopilot_runs",     "cols": ["id", "fired_at", "fired_date", "total_queued", "status"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              "key": ("fired_date",),     "has_id": True},
    {"name": "blocklist",          "cols": ["id", "kind", "value", "reason", "created_at"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         "key": ("kind", "value"),    "has_id": True},
    {"name": "company_enrichment", "cols": ["company", "summary", "source", "fetched_at"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          "key": ("company",),        "has_id": False},
    {"name": "cvs",                "cols": ["id", "cluster", "filename", "stored_path", "size_bytes", "uploaded_at"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               "key": ("cluster",),        "has_id": True},
    {"name": "extension_keys",     "cols": ["key", "label", "created_at", "last_used_at"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          "key": ("key",),            "has_id": False},
    {"name": "gmail_accounts",     "cols": ["id", "email", "app_password_enc", "display_name", "daily_cap", "sent_today", "sent_date", "last_sent_at", "imap_uid_seen", "status", "connected_at", "last_verified_at", "warmup_enabled", "warmup_start_date", "consecutive_failures", "bounce_count_today", "paused_reason"],                                                                                                                                                                                                                                                                                                                                       "key": ("email",),          "has_id": True},
    {"name": "kv_settings",        "cols": ["key", "value", "updated_at"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          "key": ("key",),            "has_id": False},
    {"name": "safety_state",       "cols": ["id", "daily_sent_count", "daily_sent_date", "last_send_at", "consecutive_failures", "warning_paused_until", "autopilot_enabled", "autopilot_hour", "safety_mode", "warmup_curve_json", "autopilot_tz", "business_hours_only", "autopilot_minute", "autopilot_count", "followups_autopilot", "followups_hour"],                                                                                                                                                                                                                                                                                                          "key": ("id",),             "has_id": False},
    {"name": "recyclebin",         "cols": ["id", "original_id", "post_url", "payload_json", "reason", "moved_at"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 "key": ("original_id", "post_url"), "has_id": True},

    # Parent of replies/followups/email_opens
    {"name": "leads",              "cols": ["id", "post_url", "posted_by", "company", "role", "tech_stack", "rate", "location", "tags", "post_text", "email", "phone", "status", "gen_subject", "gen_body", "email_mode", "cv_cluster", "jaydip_note", "skip_reason", "skip_source", "first_seen_at", "last_seen_at", "queued_at", "sent_at", "replied_at", "bounced_at", "follow_up_at", "needs_attention", "sent_message_id", "sent_via_account_id", "call_status", "reviewed_at", "open_token", "open_count", "first_opened_at", "last_opened_at", "scheduled_send_at", "ooo_nudge_at", "ooo_nudge_sent_at", "fit_score", "fit_score_reasons", "remind_at"], "key": ("post_url",),       "has_id": True},

    # FK -> leads.id
    {"name": "replies",            "cols": ["id", "lead_id", "gmail_msg_id", "gmail_thread_id", "from_email", "subject", "snippet", "received_at", "kind", "handled_at", "sentiment", "body", "auto_draft_body", "auto_draft_at", "intent"],                                                                                                                                                                                                                                                                                                                                                                                                                        "key": ("gmail_msg_id",),   "has_id": True},
    {"name": "followups",          "cols": ["id", "lead_id", "sequence", "message_id", "sent_at"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  "key": None,                "has_id": True},
    {"name": "email_opens",        "cols": ["id", "lead_id", "opened_at", "user_agent", "ip"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      "key": None,                "has_id": True},

    # Keyless audit log
    {"name": "events",             "cols": ["id", "at", "kind", "lead_id", "meta_json"],                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "key": None,                "has_id": True},
]


def open_sqlite(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_rows(conn: sqlite3.Connection, table: str, cols: Sequence[str]) -> list[tuple]:
    """Return rows in `cols` order. Skip if table is missing."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    if cur.fetchone() is None:
        return []
    col_list = ", ".join(f'"{c}"' for c in cols)
    rows = conn.execute(f"SELECT {col_list} FROM {table}").fetchall()
    return [tuple(r) for r in rows]


def merge(
    live_rows: Iterable[tuple],
    backup_rows: Iterable[tuple],
    cols: Sequence[str],
    key: tuple[str, ...] | None,
) -> tuple[list[tuple], int]:
    """Combine live + backup. Live wins on conflicts.

    If key is given, dedupe by that subset of columns.
    Otherwise dedupe by the full row content (excluding `id` if present).
    Returns (merged_rows, backup_only_count).
    """
    if key is not None:
        idx = [cols.index(c) for c in key]
        seen: dict[tuple, tuple] = {}
        for r in live_rows:
            seen[tuple(r[i] for i in idx)] = r
        backup_only = 0
        for r in backup_rows:
            k = tuple(r[i] for i in idx)
            if k not in seen:
                seen[k] = r
                backup_only += 1
        return list(seen.values()), backup_only

    # Keyless: keep all live rows verbatim (duplicates are real — e.g. multiple
    # 'ingest' events with the same timestamp). For backup, top up only the
    # extras: if backup has N copies of a content tuple and live has M < N,
    # append the (N - M) missing copies with a null id so postgres assigns one.
    from collections import Counter

    id_idx = cols.index("id") if "id" in cols else None

    def content(r: tuple) -> tuple:
        if id_idx is None:
            return r
        return tuple(v for i, v in enumerate(r) if i != id_idx)

    out: list[tuple] = list(live_rows)
    live_counter: Counter = Counter(content(r) for r in live_rows)
    backup_counter: Counter = Counter(content(r) for r in backup_rows)

    backup_only = 0
    # Walk backup rows in original order so we keep deterministic output.
    remaining_in_live = dict(live_counter)
    for r in backup_rows:
        c = content(r)
        if remaining_in_live.get(c, 0) > 0:
            remaining_in_live[c] -= 1
            continue
        # This backup copy is genuinely extra — add it with a fresh id.
        if id_idx is not None:
            r = tuple(None if i == id_idx else v for i, v in enumerate(r))
        out.append(r)
        backup_only += 1
    return out, backup_only


def insert(pg_cur, table: str, cols: Sequence[str], rows: list[tuple]) -> None:
    if not rows:
        return
    # If `id` is present, only include columns where the value is not None.
    # Easiest: split rows into (with-id) and (without-id) buckets.
    if "id" in cols:
        id_idx = cols.index("id")
        with_id = [r for r in rows if r[id_idx] is not None]
        without_id = [r for r in rows if r[id_idx] is None]

        if with_id:
            placeholders = ", ".join(["%s"] * len(cols))
            col_list = ", ".join(f'"{c}"' for c in cols)
            psycopg2.extras.execute_batch(
                pg_cur,
                f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})',
                with_id,
                page_size=500,
            )
        if without_id:
            cols_no_id = [c for c in cols if c != "id"]
            placeholders = ", ".join(["%s"] * len(cols_no_id))
            col_list = ", ".join(f'"{c}"' for c in cols_no_id)
            stripped = [tuple(v for i, v in enumerate(r) if i != id_idx) for r in without_id]
            psycopg2.extras.execute_batch(
                pg_cur,
                f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})',
                stripped,
                page_size=500,
            )
    else:
        placeholders = ", ".join(["%s"] * len(cols))
        col_list = ", ".join(f'"{c}"' for c in cols)
        psycopg2.extras.execute_batch(
            pg_cur,
            f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})',
            rows,
            page_size=500,
        )


def reset_sequence(pg_cur, table: str) -> None:
    pg_cur.execute(
        f"SELECT setval(pg_get_serial_sequence(%s, 'id'), "
        f"COALESCE((SELECT MAX(id) FROM \"{table}\"), 0) + 1, false)",
        (table,),
    )


def main() -> int:
    backend_dir = Path(__file__).resolve().parent.parent
    load_dotenv(backend_dir / ".env")
    db_url = os.environ["DATABASE_URL"]

    print(f"Live DB:   {LIVE_DB}")
    print(f"Backup DB: {BACKUP_DB}")
    print(f"Postgres:  {db_url.split('@', 1)[-1]}")
    print()

    live = open_sqlite(LIVE_DB)
    backup = open_sqlite(BACKUP_DB)

    pg = psycopg2.connect(db_url)
    pg.autocommit = False
    cur = pg.cursor()

    try:
        # Truncate everything in dependency order, in one shot, with sequence
        # reset. RESTART IDENTITY resets the underlying serial sequences.
        truncate_targets = ", ".join(f'"{t["name"]}"' for t in TABLES)
        cur.execute(f"TRUNCATE {truncate_targets} RESTART IDENTITY CASCADE")
        print(f"Truncated {len(TABLES)} tables.")
        print()

        totals = {"live": 0, "backup_only": 0, "merged": 0}
        for spec in TABLES:
            name = spec["name"]
            cols = spec["cols"]
            key = spec["key"]
            live_rows = fetch_rows(live, name, cols)
            backup_rows = fetch_rows(backup, name, cols)
            merged, backup_only = merge(live_rows, backup_rows, cols, key)
            insert(cur, name, cols, merged)
            if spec["has_id"]:
                reset_sequence(cur, name)
            totals["live"] += len(live_rows)
            totals["backup_only"] += backup_only
            totals["merged"] += len(merged)
            print(
                f"  {name:<22} live={len(live_rows):>5}  "
                f"backup={len(backup_rows):>5}  "
                f"backup_only={backup_only:>3}  "
                f"merged={len(merged):>5}"
            )

        pg.commit()
        print()
        print(
            f"Done. live_total={totals['live']} "
            f"backup_only_total={totals['backup_only']} "
            f"merged_total={totals['merged']}"
        )
        return 0
    except Exception:
        pg.rollback()
        raise
    finally:
        cur.close()
        pg.close()
        live.close()
        backup.close()


if __name__ == "__main__":
    sys.exit(main())
