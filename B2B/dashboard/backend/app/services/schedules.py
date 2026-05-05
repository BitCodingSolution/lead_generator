"""Daily auto-collect scheduler.

A background thread checks every 60s whether any source's schedule
window (hour:minute, local time) has been hit today and isn't yet
fired. Schedules are persisted to `schedules.json` so they survive
restart.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time

from app.config import settings
from app.services.jobs import LAST_RUNS, start_chain_job
from app.services.scrape_args import schema_flag_args
from app.services.sources import get_source


def load_schedules() -> dict:
    f = settings.schedules_file
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        # Preserve the corrupt file before returning an empty config.
        print(f"[scheduler] schedules.json corrupt ({e}); renaming to .corrupt")
        try:
            f.rename(f.with_suffix(".json.corrupt"))
        except Exception:
            pass
        return {}


def save_schedules(data: dict) -> None:
    settings.schedules_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fire_auto_collect(source_id: str) -> str | None:
    """Kick off the standard collect chain (scrape -> enrich) for a source."""
    try:
        s = get_source(source_id)
        if s.type != "grab":
            return None
        schema = s.load_schema()
        scraper_rel = (schema.get("scraper") or {}).get("path")
        if not scraper_rel:
            return None
        enricher = schema.get("enricher") or {}
        enricher_rel = enricher.get("path") or "common/enrich.py"
        enricher_default = list(enricher.get("default_args") or ["--source", source_id])

        scrape_argv = [
            settings.python_executable,
            str(settings.grab_root / scraper_rel),
            *schema_flag_args(schema, {}),
        ]
        enrich_argv = [
            settings.python_executable,
            str(settings.grab_root / enricher_rel),
            *enricher_default,
        ]
        steps = [
            {"label": "Scrape companies", "argv": scrape_argv},
            {"label": "Enrich founders", "argv": enrich_argv},
        ]
        label = f"Auto-collect: {source_id}"
        job_id = start_chain_job(steps, label)
        LAST_RUNS[source_id] = {
            "kind": "collect", "chain": steps, "label": label,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "job_id": job_id,
        }
        return job_id
    except Exception as e:
        print(f"[scheduler] fire failed for {source_id}: {e}")
        return None


def _scheduler_loop() -> None:
    while True:
        try:
            data = load_schedules()
            now = dt.datetime.now()
            today = now.date().isoformat()
            dirty = False
            for source_id, cfg in list(data.items()):
                if not cfg.get("enabled"):
                    continue
                hh = int(cfg.get("hour", 2))
                mm = int(cfg.get("minute", 0))
                if cfg.get("last_fired_date") == today:
                    continue
                if now.hour > hh or (now.hour == hh and now.minute >= mm):
                    job_id = fire_auto_collect(source_id)
                    if job_id:
                        cfg["last_fired_date"] = today
                        cfg["last_fired_at"] = now.isoformat(timespec="seconds")
                        cfg["last_job_id"] = job_id
                        dirty = True
            if dirty:
                save_schedules(data)
        except Exception as e:
            print(f"[scheduler] loop error: {e}")
        time.sleep(60)


def start_scheduler_thread() -> None:
    threading.Thread(target=_scheduler_loop, daemon=True).start()
