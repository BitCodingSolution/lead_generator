"""Batch listing / progress endpoints (Marcel + grab + per-source)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.db import conn
from app.schemas.sources import SendBatchReq
from app.services.batch_export import (
    batch_status,
    resolve_grab_batch,
    resolve_marcel_batch,
)
from app.services.jobs import start_job
from app.services.sources import all_sources, get_source

router = APIRouter(prefix="/api", tags=["batches"])


# ---- Cross-source listing ----

@router.get("/campaigns/batches")
def all_campaign_batches() -> dict:
    """Lists every batch file from every registered source."""
    known = set(all_sources().keys())
    out = []

    d = settings.grab_batches_dir
    for f in d.glob("*.xlsx"):
        stem = f.stem
        parts = stem.split("_")
        source_id = None
        if len(parts) >= 3:
            for sid in known:
                prefix = f"{parts[0]}_{sid}_"
                if stem.startswith(prefix):
                    source_id = sid
                    break
        if source_id is None:
            continue
        stat = f.stat()
        out.append({
            "name": f.name, "path": str(f), "source": source_id,
            "size_kb": round(stat.st_size / 1024),
            "created_at": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            **batch_status(f),
        })

    marcel_dir = settings.batches_dir
    if marcel_dir.exists():
        for f in marcel_dir.glob("*.xlsx"):
            stat = f.stat()
            out.append({
                "name": f.name, "path": str(f), "source": "marcel",
                "size_kb": round(stat.st_size / 1024),
                "created_at": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                **batch_status(f),
            })

    out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"batches": out, "count": len(out)}


# ---- Per-source batches ----

@router.get("/sources/{source_id}/batches")
def source_batches(source_id: str) -> dict:
    get_source(source_id)
    d = settings.grab_batches_dir
    out = []
    for f in sorted(d.glob(f"*_{source_id}_*.xlsx"), reverse=True):
        stat = f.stat()
        out.append({
            "name": f.name,
            "path": str(f),
            "size_kb": round(stat.st_size / 1024),
            "created_at": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            **batch_status(f),
        })
    return {"source": source_id, "batches": out, "count": len(out)}


@router.post("/sources/{source_id}/batches/{name}/generate-drafts")
def batch_generate_drafts(source_id: str, name: str) -> dict:
    p = resolve_grab_batch(source_id, name)
    drafter = settings.grab_root / "mailer" / "generate_drafts_en.py"
    argv = [settings.python_executable, str(drafter), "--file", str(p)]
    job_id = start_job(argv, f"Generate drafts: {name}")
    return {"job_id": job_id}


@router.post("/sources/{source_id}/batches/{name}/write-outlook")
def batch_write_outlook(source_id: str, name: str) -> dict:
    p = resolve_grab_batch(source_id, name)
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "write_to_outlook.py"),
        "--file", str(p),
    ]
    job_id = start_job(argv, f"Write Outlook drafts: {name}")
    return {"job_id": job_id}


@router.post("/sources/{source_id}/batches/{name}/send")
def batch_send(source_id: str, name: str, req: SendBatchReq) -> dict:
    p = resolve_grab_batch(source_id, name)
    status = batch_status(p)
    total = status.get("total") or 0
    sent = status.get("sent") or 0
    remaining = max(0, total - sent)
    if remaining == 0:
        raise HTTPException(400, f"Batch '{name}' is fully sent ({sent}/{total}).")
    count = max(1, min(int(req.count), remaining))
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "send_drafts.py"),
        "--file", str(p), "--count", str(count),
    ]
    job_id = start_job(argv, f"Send {count} drafts: {name}")
    return {"job_id": job_id, "count": count, "remaining_before": remaining}


@router.delete("/sources/{source_id}/batches/{name}")
def batch_delete(source_id: str, name: str) -> dict:
    p = resolve_grab_batch(source_id, name)
    p.unlink()
    return {"ok": True, "deleted": name}


# ---- Marcel daily batches ----

@router.get("/batches")
def batches(limit: int = 20) -> list[dict]:
    """Marcel daily batches summary from the daily_batches table."""
    from app.db import q_all
    return q_all(
        "SELECT * FROM daily_batches ORDER BY batch_date DESC LIMIT ?", limit
    )


@router.get("/batches/files")
def batch_files() -> list[dict]:
    if not settings.batches_dir.exists():
        return []
    out = []
    for f in sorted(settings.batches_dir.glob("*.xlsx"), reverse=True):
        out.append({
            "name": f.name,
            "size_kb": round(f.stat().st_size / 1024),
            "modified": dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
        })
    return out


@router.get("/batches/progress")
def batch_progress(file: str) -> dict:
    """Per-batch counts from the xlsx + DB (DB is the source of truth)."""
    path = resolve_marcel_batch(file)
    import pandas as pd
    df = pd.read_excel(path)
    total = len(df)
    lead_ids = df['lead_id'].dropna().astype(str).tolist()
    if not lead_ids:
        return {
            "file": file, "total": total, "drafted": 0, "in_outlook": 0, "sent": 0,
            "pending_draft": total, "pending_outlook": 0, "pending_send": 0,
        }
    placeholders = ",".join(["?"] * len(lead_ids))
    c = conn()
    try:
        drafted = c.execute(
            f"SELECT COUNT(DISTINCT lead_id) FROM emails_sent WHERE lead_id IN ({placeholders})",
            lead_ids,
        ).fetchone()[0]
        in_outlook = c.execute(
            f"SELECT COUNT(DISTINCT lead_id) FROM emails_sent "
            f"WHERE lead_id IN ({placeholders}) AND outlook_entry_id IS NOT NULL",
            lead_ids,
        ).fetchone()[0]
        sent = c.execute(
            f"SELECT COUNT(DISTINCT lead_id) FROM emails_sent "
            f"WHERE lead_id IN ({placeholders}) AND sent_at IS NOT NULL",
            lead_ids,
        ).fetchone()[0]
    finally:
        c.close()
    return {
        "file": file,
        "total": total,
        "drafted": drafted,
        "in_outlook": in_outlook,
        "sent": sent,
        "pending_draft": total - drafted,
        "pending_outlook": drafted - in_outlook,
        "pending_send": in_outlook - sent,
    }
