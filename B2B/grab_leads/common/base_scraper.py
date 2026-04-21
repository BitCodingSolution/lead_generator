"""
BaseScraper — minimal contract every source scraper implements.

Kept intentionally thin so Phase 1 ships fast. Add more lifecycle hooks
(checkpoint, resume, proxy rotation, etc.) only when a second source actually
needs them — YAGNI.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


LEADS_DDL = """
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    company_name    TEXT NOT NULL,
    company_domain  TEXT,
    company_size    TEXT,
    location        TEXT,
    signal_type     TEXT NOT NULL,
    signal_detail   TEXT,
    signal_date     TEXT,
    person_name     TEXT,
    person_title    TEXT,
    person_linkedin TEXT,
    person_email    TEXT,
    email_verified  INTEGER DEFAULT 0,
    extra_data      TEXT,
    scraped_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    first_seen_at   TEXT,
    last_seen_at    TEXT,
    needs_attention INTEGER DEFAULT 0,
    UNIQUE(source, source_url)
);
CREATE INDEX IF NOT EXISTS idx_leads_signal ON leads(signal_type);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(company_domain);
"""

# Columns we track for change-detection. If any of these change between runs,
# the row is flagged `needs_attention=1` so the user sees it in the Lead Pool.
_TRACKED_FIELDS = ("signal_type", "signal_detail", "company_size", "location")
# Tracked keys inside extra_data (JSON). Changes here also flip attention.
_TRACKED_EXTRA_KEYS = ("is_hiring", "batch", "team_size", "status")


class BaseScraper(ABC):
    source_name: str = ""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "raw").mkdir(exist_ok=True)
        self.db_path = self.data_dir / "data.db"
        self._init_db()
        self.log = self._make_logger()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as con:
            con.executescript(LEADS_DDL)
            # Non-destructive migrations for older DBs: add columns if missing,
            # then create index that depends on them.
            cols = {r[1] for r in con.execute("PRAGMA table_info(leads)").fetchall()}
            for col, decl in (
                ("first_seen_at", "TEXT"),
                ("last_seen_at", "TEXT"),
                ("needs_attention", "INTEGER DEFAULT 0"),
            ):
                if col not in cols:
                    con.execute(f"ALTER TABLE leads ADD COLUMN {col} {decl}")
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_leads_attention ON leads(needs_attention)"
            )
            # Backfill timestamps for rows that existed before the migration so
            # freshness filters work meaningfully on legacy data.
            con.execute(
                "UPDATE leads SET first_seen_at = scraped_at "
                "WHERE first_seen_at IS NULL AND scraped_at IS NOT NULL"
            )
            con.execute(
                "UPDATE leads SET last_seen_at = scraped_at "
                "WHERE last_seen_at IS NULL AND scraped_at IS NOT NULL"
            )

    def _make_logger(self) -> logging.Logger:
        logs_dir = Path(__file__).resolve().parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = logs_dir / f"{self.source_name}_{ts}.log"
        logger = logging.getLogger(self.source_name)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(fh)
        logger.addHandler(sh)
        return logger

    @abstractmethod
    def scrape(self, **kwargs) -> Iterable[dict]:
        """Yield lead dicts. Keys should match the leads table columns;
        anything extra goes into `extra_data` (JSON)."""

    def save_raw(self, name: str, payload) -> None:
        """Dump a raw JSON blob for audit/replay."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.data_dir / "raw" / f"{name}_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def upsert(self, lead: dict) -> str:
        """Insert new / update changed / touch unchanged rows.

        Returns one of: "inserted" | "updated" | "unchanged".
        On insert and on material change, `needs_attention=1` is set so the
        UI's "Needs attention" filter surfaces the row for outreach.
        """
        known = {
            "source", "source_url", "company_name", "company_domain", "company_size",
            "location", "signal_type", "signal_detail", "signal_date",
            "person_name", "person_title", "person_linkedin", "person_email",
            "email_verified",
        }
        core = {k: lead.get(k) for k in known}
        core["source"] = core.get("source") or self.source_name
        extras = {k: v for k, v in lead.items() if k not in known}
        extra_json = json.dumps(extras, ensure_ascii=False) if extras else None

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with sqlite3.connect(self.db_path) as con:
            row = con.execute(
                "SELECT id, signal_type, signal_detail, company_size, location, "
                "extra_data FROM leads WHERE source = ? AND source_url = ?",
                (core["source"], core["source_url"]),
            ).fetchone()

            if row is None:
                # New: first_seen = last_seen = now, flag for attention.
                core["extra_data"] = extra_json
                core["first_seen_at"] = now
                core["last_seen_at"] = now
                core["needs_attention"] = 1
                cols = ",".join(core.keys())
                placeholders = ",".join("?" * len(core))
                con.execute(
                    f"INSERT INTO leads ({cols}) VALUES ({placeholders})",
                    list(core.values()),
                )
                return "inserted"

            # Existing: compare tracked fields.
            lead_id, old_sig, old_detail, old_size, old_loc, old_extra_raw = row
            try:
                old_extra = json.loads(old_extra_raw) if old_extra_raw else {}
            except Exception:
                old_extra = {}

            changed = False
            for field, old_val in (
                ("signal_type", old_sig),
                ("signal_detail", old_detail),
                ("company_size", old_size),
                ("location", old_loc),
            ):
                if (core.get(field) or "") != (old_val or ""):
                    changed = True
                    break
            if not changed:
                for k in _TRACKED_EXTRA_KEYS:
                    if (extras.get(k) if extras else None) != old_extra.get(k):
                        changed = True
                        break

            if changed:
                con.execute(
                    "UPDATE leads SET signal_type=?, signal_detail=?, company_size=?, "
                    "location=?, extra_data=?, last_seen_at=?, needs_attention=1 "
                    "WHERE id=?",
                    (
                        core.get("signal_type"),
                        core.get("signal_detail"),
                        core.get("company_size"),
                        core.get("location"),
                        extra_json,
                        now,
                        lead_id,
                    ),
                )
                return "updated"

            # Unchanged: just touch last_seen_at.
            con.execute(
                "UPDATE leads SET last_seen_at=? WHERE id=?", (now, lead_id)
            )
            return "unchanged"

    def run(self, **kwargs) -> dict:
        """Convenience wrapper: iterate scrape() and upsert each lead."""
        inserted = updated = unchanged = 0
        started = datetime.now(timezone.utc).isoformat()
        for lead in self.scrape(**kwargs):
            result = self.upsert(lead)
            if result == "inserted":
                inserted += 1
            elif result == "updated":
                updated += 1
            else:
                unchanged += 1
        stats = {
            "source": self.source_name,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged,
        }
        self.log.info("Run complete: %s", stats)
        return stats
