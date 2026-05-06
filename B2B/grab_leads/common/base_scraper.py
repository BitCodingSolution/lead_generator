"""BaseScraper — minimal contract every grab-source scraper implements.

Subclasses declare:
    source_name    — slug for the `source` column (e.g. "ycombinator")
    leads_model    — SQLAlchemy ORM class for the per-source leads table
                     (e.g. `app.yc.models.YcLead`)

The model must expose: `id`, `source`, `source_url`, `company_name`,
`company_domain`, `company_size`, `location`, `signal_type`,
`signal_detail`, `signal_date`, `person_name`, `person_title`,
`person_linkedin`, `person_email`, `email_verified`, `extra_data`,
`scraped_at`, `first_seen_at`, `last_seen_at`, `needs_attention`.

Storage is the backend's shared Postgres engine (via `common.db`),
so scrapers automatically join the same Alembic-managed schema as
the dashboard. Raw API dumps still go to `data_dir/raw/*.json` for
audit / replay; logs to `grab_leads/logs/*.log`.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from common.db import session_scope


# Columns we track for change-detection. If any of these change between runs,
# the row is flagged `needs_attention=1` so the user sees it in the Lead Pool.
_TRACKED_FIELDS = ("signal_type", "signal_detail", "company_size", "location")
# Tracked keys inside extra_data (JSON). Changes here also flip attention.
_TRACKED_EXTRA_KEYS = ("is_hiring", "batch", "team_size", "status")

# Columns the upsert is allowed to write directly. Any extra keys in the
# scraped lead dict spill into `extra_data` as JSON.
_KNOWN_COLS = (
    "source", "source_url", "company_name", "company_domain", "company_size",
    "location", "signal_type", "signal_detail", "signal_date",
    "person_name", "person_title", "person_linkedin", "person_email",
    "email_verified",
)


class BaseScraper(ABC):
    source_name: str = ""
    # Subclass must set this to the ORM model class for the leads table,
    # e.g. `from app.yc.models import YcLead; leads_model = YcLead`.
    leads_model: type | None = None

    def __init__(self, data_dir: Path):
        if self.leads_model is None:
            raise RuntimeError(
                f"{type(self).__name__} must set `leads_model` "
                "(e.g. `from app.yc.models import YcLead`)."
            )
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "raw").mkdir(exist_ok=True)
        self.log = self._make_logger()

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
        """Yield lead dicts. Keys should match the model columns;
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
        Model = self.leads_model
        core = {k: lead.get(k) for k in _KNOWN_COLS}
        core["source"] = core.get("source") or self.source_name
        extras = {k: v for k, v in lead.items() if k not in _KNOWN_COLS}
        extra_json = json.dumps(extras, ensure_ascii=False) if extras else None
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with session_scope() as session:
            existing = session.execute(
                select(Model).where(
                    Model.source == core["source"],
                    Model.source_url == core["source_url"],
                )
            ).scalar_one_or_none()

            if existing is None:
                session.add(Model(
                    **core,
                    extra_data=extra_json,
                    first_seen_at=now,
                    last_seen_at=now,
                    scraped_at=now,
                    needs_attention=1,
                ))
                return "inserted"

            try:
                old_extra = json.loads(existing.extra_data) if existing.extra_data else {}
            except Exception:
                old_extra = {}

            changed = any(
                (core.get(f) or "") != (getattr(existing, f) or "")
                for f in _TRACKED_FIELDS
            ) or any(
                (extras.get(k) if extras else None) != old_extra.get(k)
                for k in _TRACKED_EXTRA_KEYS
            )

            if changed:
                existing.signal_type = core.get("signal_type")
                existing.signal_detail = core.get("signal_detail")
                existing.company_size = core.get("company_size")
                existing.location = core.get("location")
                existing.extra_data = extra_json
                existing.last_seen_at = now
                existing.needs_attention = 1
                return "updated"

            existing.last_seen_at = now
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
