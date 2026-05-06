"""One-shot data migration: Marcel SQLite → Postgres `mrc_*` tables.

Reads from `Database/Marcel Data/leads.db` and writes to the Postgres
DB pointed at by `DATABASE_URL`. Tables in dependency order so foreign
keys never reference a missing parent.

Usage:
    uv run python scripts/migrate_marcel_sqlite_to_pg.py
    uv run python scripts/migrate_marcel_sqlite_to_pg.py --force   # TRUNCATE before insert

Behaviour:
    - Default: refuses to run if any target `mrc_*` table is non-empty.
    - `--force`: TRUNCATE every `mrc_*` table (with CASCADE) before
      inserting. Use after a botched run.
    - Sequences (autoincrement ids) get reset to MAX(id)+1 after insert.
    - Inserts in 1000-row batches via psycopg2.extras.execute_values.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


SQLITE_PATH = Path(
    "/Users/jaydip/Documents/BCS/admin/lead_generator/B2B/"
    "Database/Marcel Data/leads.db"
)

# (sqlite_table, pg_table, columns_to_copy_in_order, autoinc_pk_or_None).
# Insert order respects FK dependencies — `leads` first, sidecars after.
PLAN = [
    ("leads", "mrc_leads", [
        "lead_id", "name", "salutation", "title", "company", "email", "phone",
        "xing", "linkedin", "industry", "sub_industry", "domain", "website",
        "city", "dealfront_link", "source_file", "tier", "is_owner",
        "created_at", "email_valid", "email_invalid_reason", "email_verified_at",
    ], None),
    ("lead_status", "mrc_lead_status", [
        "lead_id", "status", "touch_count", "last_touch_date", "next_action",
        "next_action_date", "first_sent_at", "assigned_to", "tags", "updated_at",
    ], None),
    ("emails_sent", "mrc_emails_sent", [
        "id", "lead_id", "batch_date", "touch_number", "subject", "body",
        "from_email", "sent_at", "outlook_entry_id", "opened", "bounced",
        "bounce_reason",
    ], "id"),
    ("replies", "mrc_replies", [
        "id", "lead_id", "reply_at", "subject", "body", "sentiment",
        "snippet", "handled", "handled_at", "my_response",
    ], "id"),
    ("daily_batches", "mrc_daily_batches", [
        "batch_date", "leads_picked", "drafts_generated", "sent_count",
        "replies_count", "notes",
    ], None),
    ("deals", "mrc_deals", [
        "id", "lead_id", "stage", "value_eur", "signed_at", "lost_reason",
    ], "id"),
    ("do_not_contact", "mrc_do_not_contact", [
        "email", "reason", "added_at",
    ], None),
    ("meetings", "mrc_meetings", [
        "id", "lead_id", "scheduled_at", "duration_min", "outcome", "notes",
    ], "id"),
    ("notes", "mrc_notes", [
        "id", "lead_id", "note", "created_at", "created_by",
    ], "id"),
]

BATCH = 1000


def main() -> None:
    force = "--force" in sys.argv[1:]

    if not SQLITE_PATH.is_file():
        sys.exit(f"SQLite file not found: {SQLITE_PATH}")

    load_dotenv()
    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        sys.exit("DATABASE_URL not set (need it to reach Postgres).")

    sql = sqlite3.connect(str(SQLITE_PATH))
    sql.row_factory = sqlite3.Row
    pg = psycopg2.connect(pg_url)
    pg.autocommit = False

    try:
        with pg.cursor() as cur:
            # 1. Pre-flight: confirm targets are empty (or --force).
            non_empty = []
            for _, pg_table, _, _ in PLAN:
                cur.execute(f"SELECT COUNT(*) FROM {pg_table}")
                n = cur.fetchone()[0]
                if n > 0:
                    non_empty.append((pg_table, n))
            if non_empty and not force:
                pg.rollback()
                msg = "\n  ".join(f"{t}: {n} rows" for t, n in non_empty)
                sys.exit(
                    f"Refusing to migrate — target tables are not empty:\n  {msg}\n"
                    f"Re-run with --force to TRUNCATE first."
                )

            if force and non_empty:
                # CASCADE because the sidecar tables FK back to mrc_leads.
                tables = ", ".join(t for _, t, _, _ in PLAN)
                print(f"[force] TRUNCATE {tables} CASCADE")
                cur.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")

        pg.commit()

        # 2. Bulk-copy each table.
        totals = {}
        for sqlite_table, pg_table, cols, autoinc in PLAN:
            t0 = time.monotonic()
            n_rows = sql.execute(
                f"SELECT COUNT(*) FROM {sqlite_table}"
            ).fetchone()[0]
            if n_rows == 0:
                print(f"  {pg_table:25s}  (empty)")
                totals[pg_table] = 0
                continue

            collist = ", ".join(cols)
            select_sql = f"SELECT {collist} FROM {sqlite_table}"
            cursor = sql.execute(select_sql)

            insert_sql = f"INSERT INTO {pg_table} ({collist}) VALUES %s"
            written = 0
            with pg.cursor() as cur:
                while True:
                    chunk = cursor.fetchmany(BATCH)
                    if not chunk:
                        break
                    rows = [tuple(r[c] for c in cols) for r in chunk]
                    psycopg2.extras.execute_values(
                        cur, insert_sql, rows, page_size=BATCH,
                    )
                    written += len(rows)
                pg.commit()
            elapsed = time.monotonic() - t0
            rate = written / elapsed if elapsed > 0 else 0
            print(
                f"  {pg_table:25s}  {written:>7d} rows in "
                f"{elapsed:5.2f}s ({rate:>6.0f}/s)"
            )
            totals[pg_table] = written

            # 3. After inserting an autoinc-PK table, reset the PG sequence
            #    so the next INSERT (without supplying id) doesn't collide.
            if autoinc:
                with pg.cursor() as cur:
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence(%s, %s), "
                        f"COALESCE((SELECT MAX({autoinc}) FROM {pg_table}), 1))",
                        (pg_table, autoinc),
                    )
                pg.commit()

        # 4. Final verification: row counts must match.
        print("\nVerification (sqlite ↔ postgres):")
        ok = True
        for sqlite_table, pg_table, _, _ in PLAN:
            n_sql = sql.execute(
                f"SELECT COUNT(*) FROM {sqlite_table}"
            ).fetchone()[0]
            with pg.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {pg_table}")
                n_pg = cur.fetchone()[0]
            mark = "✓" if n_sql == n_pg else "✗"
            if n_sql != n_pg:
                ok = False
            print(f"  [{mark}] {sqlite_table:18s} → {pg_table:25s} {n_sql:>7d} → {n_pg:>7d}")
        if not ok:
            sys.exit(1)
        print("\nAll counts match.")

    finally:
        sql.close()
        pg.close()


if __name__ == "__main__":
    main()
