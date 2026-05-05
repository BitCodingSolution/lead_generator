"""Long-running job tracker.

In-memory registry of background subprocess + chain jobs. Each job has
its own status, log buffer, and optional Popen handle for stop. Jobs are
identified by a short random UUID prefix. Finished jobs are evicted
after `settings.job_retention_seconds` to bound memory growth.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import threading
import uuid
from typing import Callable

from app.config import settings

# ---- Public state ----
JOBS: dict[str, dict] = {}
LAST_RUNS: dict[str, dict] = {}

ChainStep = dict  # {"label": str, "argv": [...]} | {"label": str, "callable": fn}


def _evict_old_jobs() -> None:
    """Drop finished jobs older than retention to bound memory."""
    now = dt.datetime.now()
    cutoff = settings.job_retention_seconds
    for jid in list(JOBS.keys()):
        j = JOBS[jid]
        if j.get("status") in ("done", "error"):
            ended = j.get("ended_at")
            if not ended:
                continue
            try:
                age = (now - dt.datetime.fromisoformat(ended)).total_seconds()
            except Exception:
                continue
            if age > cutoff:
                JOBS.pop(jid, None)


def pipeline_running() -> bool:
    return any(
        j.get("status") in ("queued", "running")
        and str(j.get("label", "")).startswith("Pipeline:")
        for j in JOBS.values()
    )


# ---- Single-script job ----

def _run_script_job(job_id: str, argv: list[str]) -> None:
    JOBS[job_id]["status"] = "running"
    JOBS[job_id]["logs"] = []
    proc = None
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(settings.base_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        JOBS[job_id]["pid"] = proc.pid
        JOBS[job_id]["proc"] = proc
        for line in proc.stdout:
            JOBS[job_id]["logs"].append(line.rstrip())
            if len(JOBS[job_id]["logs"]) > 2000:
                JOBS[job_id]["logs"] = JOBS[job_id]["logs"][-1500:]
        rc = proc.wait()
        if JOBS[job_id].get("stop_requested"):
            JOBS[job_id]["status"] = "stopped"
        else:
            JOBS[job_id]["status"] = "done" if rc == 0 else "error"
        JOBS[job_id]["returncode"] = rc
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
    finally:
        JOBS[job_id]["ended_at"] = dt.datetime.now().isoformat(timespec="seconds")
        JOBS[job_id].pop("proc", None)
        _evict_old_jobs()


def start_job(argv: list[str], label: str) -> str:
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "label": label,
        "argv": argv,
        "status": "queued",
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "logs": [],
    }
    threading.Thread(target=_run_script_job, args=(job_id, argv), daemon=True).start()
    return job_id


# ---- Chain job: one job_id, N sequential steps (argv or callable) ----

def _run_chain_job(job_id: str, steps: list[ChainStep]) -> None:
    JOBS[job_id]["status"] = "running"
    JOBS[job_id]["logs"] = []
    JOBS[job_id]["step_total"] = len(steps)
    JOBS[job_id]["step_index"] = 0
    JOBS[job_id]["step_label"] = ""

    def _log(s: str) -> None:
        JOBS[job_id]["logs"].append(s)
        if len(JOBS[job_id]["logs"]) > 3000:
            JOBS[job_id]["logs"] = JOBS[job_id]["logs"][-2500:]

    try:
        for idx, step in enumerate(steps, start=1):
            if JOBS[job_id].get("stop_requested"):
                JOBS[job_id]["status"] = "stopped"
                return
            JOBS[job_id]["step_index"] = idx
            JOBS[job_id]["step_label"] = step.get("label", f"step {idx}")
            _log(f"\n=== [{idx}/{len(steps)}] {JOBS[job_id]['step_label']} ===")

            argv = step.get("argv")
            fn: Callable | None = step.get("callable")
            if argv:
                try:
                    proc = subprocess.Popen(
                        argv,
                        cwd=str(settings.base_dir),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                    )
                    JOBS[job_id]["proc"] = proc
                    for line in proc.stdout:
                        _log(line.rstrip())
                    rc = proc.wait()
                    if JOBS[job_id].get("stop_requested"):
                        JOBS[job_id]["status"] = "stopped"
                        return
                    if rc != 0:
                        JOBS[job_id]["status"] = "error"
                        JOBS[job_id]["returncode"] = rc
                        _log(f"Step failed with code {rc}")
                        return
                finally:
                    JOBS[job_id].pop("proc", None)
            elif fn:
                try:
                    result = fn()
                    JOBS[job_id].setdefault("step_results", {})[
                        JOBS[job_id]["step_label"]
                    ] = result
                    _log(f"OK: {result}")
                except Exception as e:
                    if JOBS[job_id].get("stop_requested") or str(e) == "stopped":
                        JOBS[job_id]["status"] = "stopped"
                        _log("[STOPPED] by user")
                        return
                    JOBS[job_id]["status"] = "error"
                    JOBS[job_id]["error"] = str(e)
                    _log(f"Callable failed: {e}")
                    return
            else:
                JOBS[job_id]["status"] = "error"
                _log(f"Step {idx} has no argv/callable")
                return

        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["returncode"] = 0
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
        _log(f"Chain exception: {e}")
    finally:
        JOBS[job_id]["ended_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _evict_old_jobs()


def start_chain_job(steps: list[ChainStep], label: str) -> str:
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "label": label,
        "status": "queued",
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "logs": [],
        "step_total": len(steps),
        "step_index": 0,
        "step_label": "",
    }
    threading.Thread(target=_run_chain_job, args=(job_id, steps), daemon=True).start()
    return job_id


# ---- Cooperative stop ----

def request_stop(job_id: str) -> dict:
    j = JOBS.get(job_id)
    if not j:
        return {"ok": False, "error": "job not found", "status": 404}
    if j.get("status") not in ("queued", "running"):
        return {"ok": False, "status": j.get("status"), "note": "job already finished"}
    j["stop_requested"] = True
    proc = j.get("proc")
    if proc is not None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                )
            else:
                proc.terminate()
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True, "job_id": job_id, "stopped": True}


# ---- Progress parsing for scrape/enrich logs ----

_SCRAPE_PAGE = re.compile(r"Fetching page (\d+)")
_SCRAPE_TOTAL = re.compile(r"Total matches: (\d+) \(across (\d+) pages\)")
_ENRICH_TOTAL = re.compile(r"Processing (\d+) companies")
_ENRICH_ROW = re.compile(r"^\s*\[(\d+)\]")


def parse_progress(kind: str, logs: list[str]) -> dict:
    total: int | None = None
    current: int | None = None
    last_line = (logs[-1] if logs else "")[:200]

    if kind == "scrape":
        for line in logs:
            m = _SCRAPE_TOTAL.search(line)
            if m:
                total = int(m.group(2))
        pages_seen = [int(m.group(1)) for line in logs if (m := _SCRAPE_PAGE.search(line))]
        if pages_seen:
            current = max(pages_seen) + 1
        unit = "pages"
    elif kind == "enrich":
        for line in logs:
            m = _ENRICH_TOTAL.search(line)
            if m:
                total = int(m.group(1))
        rows_seen = [int(m.group(1)) for line in logs if (m := _ENRICH_ROW.search(line))]
        if rows_seen:
            current = max(rows_seen)
        unit = "companies"
    else:
        unit = ""

    percent = None
    if total and current is not None:
        percent = min(100, round((current / total) * 100))
    return {
        "current": current,
        "total": total,
        "percent": percent,
        "unit": unit,
        "last_line": last_line,
    }
