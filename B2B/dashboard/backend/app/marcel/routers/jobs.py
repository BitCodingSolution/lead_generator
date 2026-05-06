"""Job tracker endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.marcel.services.jobs import JOBS, request_stop

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def jobs() -> list[dict]:
    return sorted(JOBS.values(), key=lambda j: j.get("started_at", ""), reverse=True)[:30]


@router.get("/{job_id}")
def job_status(job_id: str) -> dict:
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404)
    out = {k: v for k, v in j.items() if k not in ("proc",)}
    out["logs"] = j.get("logs", [])[-200:]
    return out


@router.post("/{job_id}/stop")
def job_stop(job_id: str) -> dict:
    res = request_stop(job_id)
    if res.get("status") == 404:
        raise HTTPException(404, "job not found")
    return res
