"""
Enrichment orchestrator: lead (company) -> founders -> email candidates -> verify.

Reads companies from a source data.db (leads table) and writes results to a
new `founders` table in the same DB.

Usage:
    python common/enrich.py --source ycombinator --limit 10
    python common/enrich.py --source ycombinator --limit 10 --dry-run
    python common/enrich.py --source ycombinator --only-missing

Schema extension (created on first run):
    CREATE TABLE founders (
        id INTEGER PK, lead_id INTEGER FK,
        full_name, first_name, last_name, title,
        linkedin_url, twitter_url, bio,
        email, email_status, email_mx,
        candidates_tried (JSON), extra_data (JSON),
        enriched_at
    )
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import smtp_verify
from common.decision_maker_finder import fetch_yc_detail, Person, CompanyMeta
from common.email_pattern_gen import generate as gen_emails
from dataclasses import asdict


FOUNDERS_DDL = """
CREATE TABLE IF NOT EXISTS founders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id           INTEGER NOT NULL,
    full_name         TEXT,
    first_name        TEXT,
    last_name         TEXT,
    title             TEXT,
    linkedin_url      TEXT,
    twitter_url       TEXT,
    bio               TEXT,
    email             TEXT,
    email_status      TEXT,
    email_mx          TEXT,
    candidates_tried  TEXT,
    extra_data        TEXT,
    enriched_at       TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    UNIQUE(lead_id, full_name)
);
CREATE INDEX IF NOT EXISTS idx_founders_lead ON founders(lead_id);
CREATE INDEX IF NOT EXISTS idx_founders_email_status ON founders(email_status);
"""


def _db_path(source: str) -> Path:
    return Path(__file__).resolve().parents[1] / "sources" / source / "data.db"


def _slug_from_extra(extra_json: str | None) -> str | None:
    if not extra_json:
        return None
    try:
        return json.loads(extra_json).get("slug")
    except Exception:
        return None


def _pick_best_email(candidates: list[str]) -> tuple[dict | None, list[dict]]:
    """Verify candidates in order; return first 'ok' verdict + all tried."""
    tried: list[dict] = []
    best: dict | None = None
    for email in candidates:
        verdict = smtp_verify.verify(email)
        tried.append(verdict)
        if verdict["status"] == "ok" and best is None:
            best = verdict
            # keep verifying rest to know domain behaviour? No — one ok is enough.
            break
    return best, tried


def fetch_for_lead(source: str, lead_row: sqlite3.Row) -> tuple[list, object | None]:
    """Return (founders, company_meta_or_None)."""
    if source == "ycombinator":
        slug = _slug_from_extra(lead_row["extra_data"])
        if not slug:
            return [], None
        return fetch_yc_detail(slug)
    raise NotImplementedError(f"No fetcher for source '{source}'")


def _merge_company_meta(con: sqlite3.Connection, lead_id: int, meta: CompanyMeta) -> None:
    """Merge CompanyMeta fields into the lead's extra_data JSON blob."""
    if meta is None:
        return
    row = con.execute("SELECT extra_data FROM leads WHERE id=?", (lead_id,)).fetchone()
    try:
        existing = json.loads(row["extra_data"] or "{}") if row else {}
    except Exception:
        existing = {}
    meta_dict = {k: v for k, v in asdict(meta).items() if v not in (None, [], {})}
    # Namespace company meta under a single key to avoid clashing with
    # scraper-originated fields (industry, tags, etc.).
    existing["company_meta"] = {**(existing.get("company_meta") or {}), **meta_dict}
    con.execute(
        "UPDATE leads SET extra_data=? WHERE id=?",
        (json.dumps(existing, ensure_ascii=False), lead_id),
    )


def enrich(
    source: str,
    limit: int | None = None,
    only_missing: bool = True,
    dry_run: bool = False,
    sleep_between: float = 0.5,
) -> dict:
    db = _db_path(source)
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript(FOUNDERS_DDL)

    if only_missing:
        q = """
        SELECT l.* FROM leads l
        LEFT JOIN founders f ON f.lead_id = l.id
        WHERE f.id IS NULL
        """
    else:
        q = "SELECT * FROM leads l"
    if limit:
        q += f" LIMIT {int(limit)}"
    leads = list(con.execute(q))

    stats = {"companies_processed": 0, "founders_found": 0, "emails_verified": 0, "errors": 0}
    print(f"Processing {len(leads)} companies from {source}...")

    for row in leads:
        stats["companies_processed"] += 1
        lead_id = row["id"]
        name = row["company_name"]
        domain = row["company_domain"]
        try:
            people, meta = fetch_for_lead(source, row)
        except Exception as e:
            stats["errors"] += 1
            print(f"  [{lead_id}] {name}: fetch error: {e}")
            continue

        # Persist company meta (socials, year_founded, YC videos, etc.) to
        # the lead row — even if no founders, meta is still valuable.
        if meta and not dry_run:
            try:
                _merge_company_meta(con, lead_id, meta)
                con.commit()
            except Exception as e:
                print(f"  [{lead_id}] {name}: meta merge failed: {e}")

        if not people:
            print(f"  [{lead_id}] {name}: no founders found (meta stored)")
            continue

        for p in people:
            stats["founders_found"] += 1
            candidates = gen_emails(p.first_name, p.last_name, domain or "") if domain else []
            best, tried = _pick_best_email(candidates)
            if best:
                stats["emails_verified"] += 1

            print(
                f"  [{lead_id}] {name:<25}  {p.full_name:<25}  {p.title or '-':<20}  "
                f"{(best['email'] if best else '— no verified —')}"
            )

            if dry_run:
                continue

            con.execute(
                """INSERT OR IGNORE INTO founders
                   (lead_id, full_name, first_name, last_name, title,
                    linkedin_url, twitter_url, bio,
                    email, email_status, email_mx,
                    candidates_tried, extra_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    lead_id,
                    p.full_name,
                    p.first_name,
                    p.last_name,
                    p.title,
                    p.linkedin_url,
                    p.twitter_url,
                    p.bio,
                    best["email"] if best else None,
                    best["status"] if best else (tried[-1]["status"] if tried else "no_domain"),
                    best["mx_host"] if best else None,
                    json.dumps(tried, ensure_ascii=False),
                    json.dumps(p.extra, ensure_ascii=False) if p.extra else None,
                ),
            )
        con.commit()
        time.sleep(sleep_between)

    con.close()
    print("\n=== ENRICHMENT SUMMARY ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="e.g. ycombinator")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-missing", action="store_true", default=True,
                    help="Skip leads that already have founders (default: on)")
    ap.add_argument("--all", action="store_true", help="Also re-process leads with founders")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.5)
    args = ap.parse_args()

    enrich(
        source=args.source,
        limit=args.limit,
        only_missing=not args.all,
        dry_run=args.dry_run,
        sleep_between=args.sleep,
    )


if __name__ == "__main__":
    main()
