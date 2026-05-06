"""Source-scoped pipeline actions: scrape, enrich, collect (chain),
campaign (chain), export-batch, reset-all, last-run, resume, auto-run.
"""
from __future__ import annotations

import datetime as dt
import subprocess

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.marcel.schemas.sources import (
    AutoRunReq,
    CampaignReq,
    ExportBatchReq,
    SourceActionReq,
)
from app.marcel.services.batch_export import export_batch_core
from app.marcel.services.jobs import (
    JOBS,
    LAST_RUNS,
    parse_progress,
    start_chain_job,
    start_job,
)
from app.marcel.services.schedules import load_schedules, save_schedules
from app.marcel.services.scrape_args import schema_flag_args
from app.marcel.services.sources import get_source

router = APIRouter(prefix="/api/sources", tags=["source-actions"])


@router.post("/{source_id}/scrape")
def source_scrape(source_id: str, req: SourceActionReq) -> dict:
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Scrape is only available for grab-type sources")
    schema = s.load_schema()
    scraper_rel = (schema.get("scraper") or {}).get("path")
    if not scraper_rel:
        raise HTTPException(400, f"Source '{source_id}' has no scraper declared in schema.json")
    script = str(settings.grab_root / scraper_rel)
    argv = [settings.python_executable, script, *schema_flag_args(schema, req.args or {})]
    label = f"Scrape: {source_id}"
    job_id = start_job(argv, label=label)
    LAST_RUNS[source_id] = {
        "kind": "scrape", "argv": argv, "label": label,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id,
    }
    return {"job_id": job_id, "argv": argv}


@router.post("/{source_id}/enrich")
def source_enrich(source_id: str, req: SourceActionReq) -> dict:
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Enrich is only available for grab-type sources")
    schema = s.load_schema()
    enricher = schema.get("enricher") or {}
    path = enricher.get("path") or "common/enrich.py"
    default_args = list(enricher.get("default_args") or ["--source", source_id])
    extra: list[str] = []
    limit = (req.args or {}).get("limit")
    if limit:
        extra += ["--limit", str(int(limit))]
    argv = [settings.python_executable, str(settings.grab_root / path), *default_args, *extra]
    label = f"Enrich: {source_id}"
    job_id = start_job(argv, label=label)
    LAST_RUNS[source_id] = {
        "kind": "enrich", "argv": argv, "label": label,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id,
    }
    return {"job_id": job_id, "argv": argv}


@router.post("/{source_id}/collect")
def source_collect(source_id: str, req: SourceActionReq) -> dict:
    """Single server-side pipeline: scrape -> enrich. One job_id."""
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Collect is only for grab sources")
    schema = s.load_schema()
    scraper_rel = (schema.get("scraper") or {}).get("path")
    if not scraper_rel:
        raise HTTPException(400, f"Source '{source_id}' has no scraper")
    enricher = schema.get("enricher") or {}
    enricher_rel = enricher.get("path") or "common/enrich.py"
    enricher_default = list(enricher.get("default_args") or ["--source", source_id])

    PY = settings.python_executable
    grab = settings.grab_root
    scrape_argv = [PY, str(grab / scraper_rel), *schema_flag_args(schema, req.args or {})]
    enrich_argv = [PY, str(grab / enricher_rel), *enricher_default]
    limit = (req.args or {}).get("limit")
    if limit:
        enrich_argv += ["--limit", str(int(limit))]

    steps = [
        {"label": "Scrape companies", "argv": scrape_argv},
        {"label": "Enrich founders", "argv": enrich_argv},
    ]
    label = f"Collect: {source_id}"
    job_id = start_chain_job(steps, label)
    LAST_RUNS[source_id] = {
        "kind": "collect", "chain": steps, "label": label,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id,
    }
    return {"job_id": job_id, "steps": [s["label"] for s in steps]}


@router.post("/{source_id}/campaign")
def source_campaign(source_id: str, req: CampaignReq) -> dict:
    """Single server-side pipeline: export Excel -> generate drafts -> Outlook drafts."""
    if not req.lead_ids:
        raise HTTPException(400, "lead_ids is required")
    max_rows = req.max or len(req.lead_ids)

    PY = settings.python_executable
    grab = settings.grab_root

    export_result: dict = {}
    current_job_id: list[str] = [""]

    def do_export() -> str:
        res = export_batch_core(
            source_id=source_id,
            lead_ids=req.lead_ids,
            industry_tag=req.industry_tag,
            tier=req.tier,
            max_rows=max_rows,
            group_by_company=req.group_by_company,
        )
        export_result.update(res)
        return f"wrote {res['rows']} rows to {res['file_name']}"

    drafter = grab / "mailer" / "generate_drafts_en.py"
    write_outlook = settings.scripts_dir / "write_to_outlook.py"

    def _run_tracked(argv: list[str]) -> int:
        jid = current_job_id[0]
        proc = subprocess.Popen(
            argv, cwd=str(settings.base_dir), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1,
        )
        JOBS[jid]["proc"] = proc
        try:
            for line in proc.stdout:
                JOBS[jid]["logs"].append(line.rstrip())
                if len(JOBS[jid]["logs"]) > 3000:
                    JOBS[jid]["logs"] = JOBS[jid]["logs"][-2500:]
            return proc.wait()
        finally:
            JOBS[jid].pop("proc", None)

    def do_drafts() -> str:
        path = export_result.get("file")
        if not path:
            raise RuntimeError("Export produced no file")
        rc = _run_tracked([PY, str(drafter), "--file", path])
        if JOBS[current_job_id[0]].get("stop_requested"):
            raise RuntimeError("stopped")
        if rc != 0:
            raise RuntimeError(f"Drafter exited with code {rc}")
        return "drafts generated"

    def do_outlook() -> str:
        path = export_result.get("file")
        if not path:
            raise RuntimeError("Export produced no file")
        rc = _run_tracked([PY, str(write_outlook), "--file", path])
        if JOBS[current_job_id[0]].get("stop_requested"):
            raise RuntimeError("stopped")
        if rc != 0:
            raise RuntimeError(f"write_to_outlook exited with code {rc}")
        return f"{export_result.get('rows', 0)} drafts placed in Outlook"

    steps = [
        {"label": "Export batch", "callable": do_export},
        {"label": "Write drafts (Claude)", "callable": do_drafts},
        {"label": "Place in Outlook", "callable": do_outlook},
    ]
    label = f"Campaign: {source_id}"
    job_id = start_chain_job(steps, label)
    current_job_id[0] = job_id
    LAST_RUNS[source_id] = {
        "kind": "campaign", "label": label,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id,
    }
    return {"job_id": job_id, "steps": [s["label"] for s in steps]}


@router.post("/{source_id}/export-batch")
def source_export_batch(source_id: str, req: ExportBatchReq) -> dict:
    try:
        res = export_batch_core(
            source_id=source_id,
            lead_ids=req.lead_ids,
            industry_tag=req.industry_tag,
            tier=req.tier,
            max_rows=req.max,
            group_by_company=req.group_by_company,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    res["next_step"] = (
        "Run generate_drafts.py on this file to fill draft_subject/draft_body, "
        "then write_to_outlook.py to push to Outlook Drafts."
    )
    return res


@router.post("/{source_id}/reset-all")
def source_reset_all(source_id: str) -> dict:
    """Destructive: wipes this source's Postgres rows, raw dumps, logs, batches."""
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Reset is only for grab-type sources")

    removed = {"db_rows": 0, "raw_files": 0, "logs": 0, "batches": 0}

    # FK order: exported_leads → founders → leads. TRUNCATE … RESTART
    # IDENTITY zeroes the autoincrement sequences too — replaces the
    # legacy DELETE FROM sqlite_sequence dance.
    from app.linkedin.db import connect as _pg_connect  # local: avoid cycle
    tables_in_fk_order = [
        s.exported_table, s.founders_table, s.leads_table,
    ]
    tables_in_fk_order = [t for t in tables_in_fk_order if t]
    if tables_in_fk_order:
        try:
            with _pg_connect() as con:
                # Count first so the response can report how much was wiped.
                total = 0
                for t in tables_in_fk_order:
                    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    total += int(n or 0)
                con.execute(
                    f"TRUNCATE {', '.join(tables_in_fk_order)} "
                    f"RESTART IDENTITY CASCADE"
                )
                con.commit()
                removed["db_rows"] = total
        except Exception as e:
            raise HTTPException(500, f"Could not wipe DB: {e}")

    # Raw dumps + per-source logs still live on disk under grab_leads/.
    src_dir = settings.grab_root / "sources" / source_id
    raw_dir = src_dir / "raw"
    if raw_dir.exists():
        for f in raw_dir.glob("*.json"):
            try:
                f.unlink()
                removed["raw_files"] += 1
            except Exception:
                pass

    logs_dir = settings.grab_root / "logs"
    if logs_dir.exists():
        for f in logs_dir.glob(f"{source_id}_*.log"):
            try:
                f.unlink()
                removed["logs"] += 1
            except Exception:
                pass

    for f in settings.grab_batches_dir.glob(f"*_{source_id}_*.xlsx"):
        try:
            f.unlink()
            removed["batches"] += 1
        except Exception:
            pass

    return {"ok": True, "source": source_id, "removed": removed}


@router.get("/{source_id}/last-run")
def source_last_run(source_id: str) -> dict:
    info = LAST_RUNS.get(source_id)
    if not info:
        return {"exists": False}
    job = JOBS.get(info.get("job_id") or "")
    progress = None
    if job and job.get("status") in ("queued", "running"):
        progress = parse_progress(info.get("kind", ""), job.get("logs", []))
    return {
        "exists": True,
        "kind": info.get("kind"),
        "argv": info.get("argv"),
        "label": info.get("label"),
        "started_at": info.get("started_at"),
        "job_id": info.get("job_id"),
        "status": (job or {}).get("status"),
        "progress": progress,
    }


@router.post("/{source_id}/resume-last")
def source_resume_last(source_id: str) -> dict:
    info = LAST_RUNS.get(source_id)
    if not info or not info.get("argv"):
        raise HTTPException(400, "No previous run to resume")
    argv = list(info["argv"])
    label = f"Resume {info.get('kind','job')}: {source_id}"
    job_id = start_job(argv, label)
    LAST_RUNS[source_id] = {
        **info,
        "job_id": job_id,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    return {"job_id": job_id, "argv": argv}


@router.get("/{source_id}/auto-run")
def get_auto_run(source_id: str) -> dict:
    get_source(source_id)
    data = load_schedules()
    cfg = data.get(source_id) or {"enabled": False, "hour": 2, "minute": 0}
    now = dt.datetime.now()
    next_fire = None
    if cfg.get("enabled"):
        today_slot = now.replace(
            hour=int(cfg.get("hour", 2)),
            minute=int(cfg.get("minute", 0)),
            second=0, microsecond=0,
        )
        next_fire = (
            today_slot if today_slot > now and cfg.get("last_fired_date") != now.date().isoformat()
            else today_slot + dt.timedelta(days=1)
        ).isoformat(timespec="seconds")
    return {**cfg, "source": source_id, "next_fire": next_fire}


@router.post("/{source_id}/auto-run")
def set_auto_run(source_id: str, req: AutoRunReq) -> dict:
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Auto-run is only for grab sources")
    if not (0 <= req.hour <= 23 and 0 <= req.minute <= 59):
        raise HTTPException(400, "hour/minute out of range")
    data = load_schedules()
    prior = data.get(source_id, {})
    cfg = {
        **prior,
        "enabled": bool(req.enabled),
        "hour": int(req.hour),
        "minute": int(req.minute),
    }
    if cfg["enabled"]:
        now = dt.datetime.now()
        today_slot = now.replace(
            hour=cfg["hour"], minute=cfg["minute"], second=0, microsecond=0,
        )
        if today_slot <= now:
            cfg["last_fired_date"] = now.date().isoformat()
            cfg.setdefault("last_fired_at", None)
    data[source_id] = cfg
    save_schedules(data)
    return get_auto_run(source_id)
