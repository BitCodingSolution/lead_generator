"""One-shot migration: copy YC scraper data from on-disk SQLite into Postgres.

Source: B2B/grab_leads/sources/ycombinator/data.db
Targets (all already exist in Postgres):
    leads           -> yc_leads
    founders        -> yc_founders
    exported_leads  -> yc_exported_leads
    sqlite_sequence -> yc_lead_sequence

Drops the SQLite-only `is_high_value` column on the way (no row has it set).
Resets the Postgres SERIAL sequences for yc_leads and yc_founders to MAX(id)+1
so future inserts pick up where SQLite left off.

Idempotent-ish: refuses to run if the target tables are already non-empty.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

REPO = Path(__file__).resolve().parents[3]
SQLITE_PATH = REPO / "grab_leads" / "sources" / "ycombinator" / "data.db"


def _load_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip().strip('"').strip("'")
    if url:
        return url
    env_path = REPO / "dashboard" / "backend" / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL not set")


LEADS_COLS = [
    "id", "source", "source_url", "company_name", "company_domain",
    "company_size", "location", "signal_type", "signal_detail", "signal_date",
    "person_name", "person_title", "person_linkedin", "person_email",
    "email_verified", "extra_data", "scraped_at", "first_seen_at",
    "last_seen_at", "needs_attention",
]
FOUNDER_COLS = [
    "id", "lead_id", "full_name", "first_name", "last_name", "title",
    "linkedin_url", "twitter_url", "bio", "email", "email_status", "email_mx",
    "candidates_tried", "extra_data", "enriched_at",
]
EXPORTED_COLS = ["lead_id", "founder_id", "batch_file", "exported_at"]


def _placeholders(cols: list[str]) -> str:
    return ", ".join(["%s"] * len(cols))


def _ensure_empty(pg_cur, table: str) -> None:
    pg_cur.execute(f"SELECT COUNT(*) FROM {table}")
    n = pg_cur.fetchone()[0]
    if n:
        raise SystemExit(
            f"Refusing to migrate: {table} already has {n} rows. "
            "Truncate it first if you really want to re-run."
        )


def main() -> None:
    if not SQLITE_PATH.is_file():
        raise SystemExit(f"SQLite source not found: {SQLITE_PATH}")

    sqlite_con = sqlite3.connect(str(SQLITE_PATH))
    sqlite_con.row_factory = sqlite3.Row
    pg_con = psycopg2.connect(_load_db_url())
    pg_con.autocommit = False

    try:
        with pg_con.cursor() as pg_cur:
            for t in ("yc_leads", "yc_founders", "yc_exported_leads", "yc_lead_sequence"):
                _ensure_empty(pg_cur, t)

            # ---- yc_leads ----
            rows = sqlite_con.execute(
                f"SELECT {', '.join(LEADS_COLS)} FROM leads"
            ).fetchall()
            psycopg2.extras.execute_batch(
                pg_cur,
                f"INSERT INTO yc_leads ({', '.join(LEADS_COLS)}) "
                f"VALUES ({_placeholders(LEADS_COLS)})",
                [tuple(r) for r in rows],
                page_size=500,
            )
            print(f"yc_leads: inserted {len(rows)}")

            # ---- yc_founders ----
            rows = sqlite_con.execute(
                f"SELECT {', '.join(FOUNDER_COLS)} FROM founders"
            ).fetchall()
            psycopg2.extras.execute_batch(
                pg_cur,
                f"INSERT INTO yc_founders ({', '.join(FOUNDER_COLS)}) "
                f"VALUES ({_placeholders(FOUNDER_COLS)})",
                [tuple(r) for r in rows],
                page_size=500,
            )
            print(f"yc_founders: inserted {len(rows)}")

            # ---- yc_exported_leads ----
            rows = sqlite_con.execute(
                f"SELECT {', '.join(EXPORTED_COLS)} FROM exported_leads"
            ).fetchall()
            psycopg2.extras.execute_batch(
                pg_cur,
                f"INSERT INTO yc_exported_leads ({', '.join(EXPORTED_COLS)}) "
                f"VALUES ({_placeholders(EXPORTED_COLS)})",
                [tuple(r) for r in rows],
                page_size=500,
            )
            print(f"yc_exported_leads: inserted {len(rows)}")

            # ---- yc_lead_sequence ----
            rows = sqlite_con.execute(
                "SELECT name, seq FROM sqlite_sequence"
            ).fetchall()
            psycopg2.extras.execute_batch(
                pg_cur,
                "INSERT INTO yc_lead_sequence (name, seq) VALUES (%s, %s)",
                [(r["name"], r["seq"]) for r in rows],
            )
            print(f"yc_lead_sequence: inserted {len(rows)}")

            # ---- reset SERIAL sequences so future inserts don't collide ----
            for table, seq in (
                ("yc_leads", "yc_leads_id_seq"),
                ("yc_founders", "yc_founders_id_seq"),
            ):
                pg_cur.execute(
                    f"SELECT setval('{seq}', (SELECT COALESCE(MAX(id), 0) FROM {table}))"
                )
            print("sequences: reset to MAX(id) for yc_leads, yc_founders")

        pg_con.commit()
        print("\nCommit OK.")
    except Exception:
        pg_con.rollback()
        raise
    finally:
        sqlite_con.close()
        pg_con.close()


if __name__ == "__main__":
    main()
