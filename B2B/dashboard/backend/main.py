"""
FastAPI backend for the B2B Outreach dashboard.

Run:
    python -m uvicorn dashboard.backend.main:app --reload --port 8900
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE = Path(r"H:/Lead Generator/B2B")
DB = str(BASE / "Database" / "Marcel Data" / "leads.db")
SCRIPTS = BASE / "scripts"
BATCHES_DIR = BASE / "Database" / "Marcel Data" / "01_Daily_Batches"
PY = sys.executable

DAILY_QUOTA = 25
JOB_RETENTION_SECONDS = 3600  # evict finished jobs older than 1h

app = FastAPI(title="BitCoding B2B Outreach API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- DB helpers ----
def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def q_one(sql: str, *params):
    c = conn()
    try:
        r = c.execute(sql, params).fetchone()
        return r[0] if r else 0
    finally:
        c.close()


def q_all(sql: str, *params):
    c = conn()
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


# ---- In-memory job tracker for long-running actions ----
JOBS: dict[str, dict] = {}


def run_script_job(job_id: str, argv: list[str]):
    """Run a Python script as subprocess, capture stdout live into JOBS[job_id]."""
    JOBS[job_id]["status"] = "running"
    JOBS[job_id]["logs"] = []
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(BASE),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        JOBS[job_id]["pid"] = proc.pid
        for line in proc.stdout:
            JOBS[job_id]["logs"].append(line.rstrip())
            # cap logs
            if len(JOBS[job_id]["logs"]) > 2000:
                JOBS[job_id]["logs"] = JOBS[job_id]["logs"][-1500:]
        rc = proc.wait()
        JOBS[job_id]["status"] = "done" if rc == 0 else "error"
        JOBS[job_id]["returncode"] = rc
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
    finally:
        JOBS[job_id]["ended_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _evict_old_jobs()


def _evict_old_jobs():
    """Drop finished jobs older than JOB_RETENTION_SECONDS to bound memory."""
    now = dt.datetime.now()
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
            if age > JOB_RETENTION_SECONDS:
                JOBS.pop(jid, None)


def _pipeline_running() -> bool:
    return any(
        j.get("status") in ("queued", "running")
        and str(j.get("label", "")).startswith("Pipeline:")
        for j in JOBS.values()
    )


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
    t = threading.Thread(target=run_script_job, args=(job_id, argv), daemon=True)
    t.start()
    return job_id


# ---- Read endpoints ----
@app.get("/api/stats")
def stats():
    today = dt.date.today().isoformat()
    total_sent = q_one("SELECT COUNT(*) FROM emails_sent WHERE sent_at IS NOT NULL")
    total_replies = q_one("SELECT COUNT(*) FROM replies")
    positive = q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Positive'")
    reply_rate = (total_replies / total_sent * 100) if total_sent else 0
    positive_rate = (positive / total_sent * 100) if total_sent else 0
    return {
        "total_leads": q_one("SELECT COUNT(*) FROM leads"),
        "tier1": q_one("SELECT COUNT(*) FROM leads WHERE tier=1"),
        "tier2": q_one("SELECT COUNT(*) FROM leads WHERE tier=2"),
        "new_leads": q_one("""
            SELECT COUNT(*) FROM lead_status ls
            JOIN leads l ON l.lead_id = ls.lead_id
            WHERE ls.status='New'
              AND (l.email_valid IS NULL OR l.email_valid=1)
              AND l.email NOT IN (SELECT email FROM do_not_contact)
        """),
        "invalid_emails": q_one("SELECT COUNT(*) FROM leads WHERE email_valid=0"),
        "dnc_count": q_one("SELECT COUNT(*) FROM do_not_contact"),
        "picked": q_one("SELECT COUNT(*) FROM lead_status WHERE status='Picked'"),
        "drafted": q_one(
            "SELECT COUNT(*) FROM lead_status WHERE status IN ('Drafted','DraftedInOutlook')"
        ),
        "total_sent": total_sent,
        "sent_today": q_one(
            "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today
        ),
        "total_replies": total_replies,
        "replies_today": q_one(
            "SELECT COUNT(*) FROM replies WHERE DATE(reply_at)=?", today
        ),
        "positive": positive,
        "objection": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Objection'"),
        "neutral": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Neutral'"),
        "negative": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Negative'"),
        "ooo": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='OOO'"),
        "bounce": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Bounce'"),
        "hot_pending": q_one(
            "SELECT COUNT(*) FROM replies WHERE handled=0 AND sentiment IN ('Positive','Objection')"
        ),
        "reply_rate_pct": round(reply_rate, 2),
        "positive_rate_pct": round(positive_rate, 2),
        "daily_quota": DAILY_QUOTA,
        "remaining_today": max(0, DAILY_QUOTA - q_one(
            "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today
        )),
    }


@app.get("/api/funnel")
def funnel():
    rows = q_all("SELECT status, COUNT(*) as n FROM lead_status GROUP BY status")
    by = {r["status"]: r["n"] for r in rows}
    return [
        {"stage": "New", "count": by.get("New", 0)},
        {"stage": "Picked", "count": by.get("Picked", 0)},
        {"stage": "Drafted", "count": by.get("Drafted", 0) + by.get("DraftedInOutlook", 0)},
        {"stage": "Sent", "count": by.get("Sent", 0) + sum(v for k, v in by.items() if k.startswith("Replied_"))},
        {"stage": "Replied", "count": sum(v for k, v in by.items() if k.startswith("Replied_"))},
        {"stage": "Positive", "count": by.get("Replied_Positive", 0)},
    ]


@app.get("/api/daily-activity")
def daily_activity(days: int = 30):
    sent = q_all(f"""
        SELECT DATE(sent_at) as day, COUNT(*) as sent
        FROM emails_sent
        WHERE sent_at IS NOT NULL
          AND DATE(sent_at) >= DATE('now', '-{days} days')
        GROUP BY DATE(sent_at)
    """)
    repl = q_all(f"""
        SELECT DATE(reply_at) as day, COUNT(*) as replies
        FROM replies
        WHERE DATE(reply_at) >= DATE('now', '-{days} days')
        GROUP BY DATE(reply_at)
    """)
    by = {}
    for r in sent:
        by.setdefault(r["day"], {"day": r["day"], "sent": 0, "replies": 0})["sent"] = r["sent"]
    for r in repl:
        by.setdefault(r["day"], {"day": r["day"], "sent": 0, "replies": 0})["replies"] = r["replies"]
    return sorted(by.values(), key=lambda x: x["day"])


@app.get("/api/industries")
def industries():
    return q_all("""
        SELECT l.industry, COUNT(*) as total,
          SUM(CASE WHEN ls.status='New'
                    AND (l.email_valid IS NULL OR l.email_valid=1)
                    AND l.email NOT IN (SELECT email FROM do_not_contact)
               THEN 1 ELSE 0 END) as available,
          SUM(CASE WHEN e.sent_at IS NOT NULL THEN 1 ELSE 0 END) as sent,
          l.tier as tier
        FROM leads l
        JOIN lead_status ls ON l.lead_id = ls.lead_id
        LEFT JOIN emails_sent e ON e.lead_id = l.lead_id
        WHERE l.tier IN (1, 2)
        GROUP BY l.industry, l.tier
        ORDER BY available DESC
    """)


@app.get("/api/hot-leads")
def hot_leads(limit: int = 20):
    return q_all("""
        SELECT r.id, r.lead_id, l.name, l.company, l.industry, l.city,
               r.sentiment, r.reply_at, r.snippet, r.handled
        FROM replies r JOIN leads l ON r.lead_id = l.lead_id
        WHERE r.handled = 0 AND r.sentiment IN ('Positive','Objection')
        ORDER BY r.reply_at DESC
        LIMIT ?
    """, limit)


@app.get("/api/recent-sent")
def recent_sent(limit: int = 25):
    return q_all("""
        SELECT e.sent_at, e.lead_id, l.name, l.company, l.industry, l.city,
               e.subject, ls.status
        FROM emails_sent e
        JOIN leads l ON e.lead_id = l.lead_id
        JOIN lead_status ls ON ls.lead_id = l.lead_id
        WHERE e.sent_at IS NOT NULL
        ORDER BY e.sent_at DESC
        LIMIT ?
    """, limit)


@app.get("/api/leads")
def leads(
    status: Optional[str] = None,
    industry: Optional[str] = None,
    tier: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    where = ["1=1"]
    params = []
    if status:
        where.append("ls.status = ?"); params.append(status)
    if industry:
        where.append("l.industry = ?"); params.append(industry)
    if tier:
        where.append("l.tier = ?"); params.append(tier)
    if search:
        where.append("(l.name LIKE ? OR l.company LIKE ? OR l.email LIKE ?)")
        term = f"%{search}%"
        params += [term, term, term]
    sql = f"""
        SELECT l.lead_id, l.name, l.title, l.company, l.email, l.industry, l.sub_industry,
               l.city, l.tier, ls.status, ls.touch_count, ls.last_touch_date
        FROM leads l JOIN lead_status ls ON l.lead_id = ls.lead_id
        WHERE {' AND '.join(where)}
        ORDER BY l.lead_id
        LIMIT ? OFFSET ?
    """
    params += [limit, offset]
    items = q_all(sql, *params)
    total = q_one(f"""
        SELECT COUNT(*) FROM leads l JOIN lead_status ls ON l.lead_id = ls.lead_id
        WHERE {' AND '.join(where)}
    """, *params[:-2])
    return {"items": items, "total": total}


@app.get("/api/lead/{lead_id}")
def lead_detail(lead_id: str):
    lead = q_all("""
        SELECT l.*, ls.status, ls.touch_count, ls.last_touch_date, ls.assigned_to
        FROM leads l JOIN lead_status ls ON l.lead_id = ls.lead_id
        WHERE l.lead_id = ?
    """, lead_id)
    if not lead:
        raise HTTPException(404)
    emails = q_all("SELECT * FROM emails_sent WHERE lead_id=? ORDER BY id DESC", lead_id)
    replies = q_all("SELECT * FROM replies WHERE lead_id=? ORDER BY id DESC", lead_id)
    return {"lead": lead[0], "emails": emails, "replies": replies}


@app.get("/api/batches")
def batches(limit: int = 20):
    return q_all(
        "SELECT * FROM daily_batches ORDER BY batch_date DESC LIMIT ?", limit
    )


# ---- Action endpoints ----
class PickBody(BaseModel):
    industry: str
    count: int = 10
    tier: Optional[int] = None
    city: Optional[str] = None


@app.post("/api/actions/pick-batch")
def pick_batch(body: PickBody):
    argv = [PY, str(SCRIPTS / "pick_batch.py"),
            "--industry", body.industry, "--count", str(body.count)]
    if body.tier:
        argv += ["--tier", str(body.tier)]
    if body.city:
        argv += ["--city", body.city]
    job_id = start_job(argv, f"Pick {body.count} from {body.industry}")
    return {"job_id": job_id}


class BatchFileBody(BaseModel):
    file: str  # filename only, resolved under BATCHES_DIR
    limit: Optional[int] = None


def resolve_batch(file: str) -> str:
    p = (BATCHES_DIR / file).resolve()
    if not p.exists() or not str(p).startswith(str(BATCHES_DIR)):
        raise HTTPException(400, f"Batch file not found: {file}")
    return str(p)


@app.post("/api/actions/generate-drafts")
def generate_drafts(body: BatchFileBody):
    path = resolve_batch(body.file)
    argv = [PY, str(SCRIPTS / "generate_drafts.py"), "--file", path]
    if body.limit:
        argv += ["--limit", str(body.limit)]
    return {"job_id": start_job(argv, f"Generate drafts: {body.file}")}


@app.post("/api/actions/write-outlook")
def write_outlook(body: BatchFileBody):
    path = resolve_batch(body.file)
    argv = [PY, str(SCRIPTS / "write_to_outlook.py"), "--file", path]
    return {"job_id": start_job(argv, f"Write to Outlook: {body.file}")}


class RunPipelineBody(BaseModel):
    industry: str
    count: int
    tier: Optional[int] = None
    send_mode: str = "schedule"  # "now" | "schedule" | "draft"
    no_jitter: bool = False


@app.post("/api/actions/run-pipeline")
def run_pipeline(body: RunPipelineBody):
    """Orchestrate the whole flow in one job: pick -> generate -> Outlook -> (send/schedule/draft)."""
    if body.send_mode not in ("now", "schedule", "draft"):
        raise HTTPException(400, "send_mode must be now/schedule/draft")
    if body.count <= 0:
        raise HTTPException(400, "count must be > 0")
    # Concurrency guard: only one pipeline at a time
    if _pipeline_running():
        raise HTTPException(409, "Another pipeline is already running")
    # Server-side quota enforcement for 'now' mode (schedule/draft don't send today)
    if body.send_mode == "now":
        today = dt.date.today().isoformat()
        sent_today = q_one(
            "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today
        )
        remaining = max(0, DAILY_QUOTA - sent_today)
        if body.count > remaining:
            raise HTTPException(
                400,
                f"Daily quota exceeded: {body.count} requested, {remaining} left today",
            )
    argv = [PY, str(SCRIPTS / "run_pipeline.py"),
            "--industry", body.industry,
            "--count", str(body.count),
            "--send-mode", body.send_mode]
    if body.tier:
        argv += ["--tier", str(body.tier)]
    if body.no_jitter:
        argv.append("--no-jitter")
    label = f"Pipeline: {body.industry} x {body.count} ({body.send_mode})"
    return {"job_id": start_job(argv, label)}


@app.post("/api/actions/generate-and-push")
def generate_and_push(body: BatchFileBody):
    """Run generate_drafts then write_to_outlook in one job (skip manual step 3)."""
    path = resolve_batch(body.file)
    argv = [PY, str(SCRIPTS / "generate_and_push.py"), "--file", path]
    if body.limit:
        argv += ["--limit", str(body.limit)]
    return {"job_id": start_job(argv, f"Generate+push: {body.file}")}


class SendBody(BaseModel):
    file: str
    count: int
    no_jitter: bool = False


@app.post("/api/actions/send-drafts")
def send_drafts(body: SendBody):
    path = resolve_batch(body.file)
    argv = [PY, str(SCRIPTS / "send_drafts.py"), "--file", path, "--count", str(body.count)]
    if body.no_jitter:
        argv.append("--no-jitter")
    return {"job_id": start_job(argv, f"Send {body.count} from {body.file}")}


class FollowupBody(BaseModel):
    touch: int  # 2 or 3
    days: int   # e.g. 4 or 8
    count: int = 20


@app.post("/api/actions/queue-followups")
def queue_followups(body: FollowupBody):
    if body.touch not in (2, 3):
        raise HTTPException(400, "touch must be 2 or 3")
    argv = [PY, str(SCRIPTS / "queue_followups.py"),
            "--touch", str(body.touch),
            "--days", str(body.days),
            "--count", str(body.count)]
    return {"job_id": start_job(argv, f"Queue touch-{body.touch} follow-ups (Day-{body.days})")}


@app.get("/api/pending-drafts")
def pending_drafts():
    """Drafts in Outlook that haven't been sent yet (DB view)."""
    rows = q_all("""
        SELECT e.id, e.lead_id, e.subject, l.name, l.company, l.email,
               l.industry, l.city, e.batch_date
        FROM emails_sent e
        JOIN leads l ON e.lead_id = l.lead_id
        WHERE e.sent_at IS NULL AND e.outlook_entry_id IS NOT NULL
        ORDER BY e.id DESC
    """)
    return {"count": len(rows), "items": rows}


class SendAllDraftsBody(BaseModel):
    mode: str = "schedule"  # "now" | "schedule"
    no_jitter: bool = False


@app.post("/api/actions/send-all-drafts")
def send_all_drafts(body: SendAllDraftsBody):
    """Send every pending draft in Outlook, regardless of source batch file.

    Uses DB as source of truth (send_pending.py) so drafts from any batch
    are covered. Schedule mode still gates via send_scheduler, which then
    delegates to send_pending with no --file.
    """
    total_pending = q_one(
        "SELECT COUNT(*) FROM emails_sent WHERE sent_at IS NULL AND outlook_entry_id IS NOT NULL"
    )
    if not total_pending:
        raise HTTPException(400, "No pending drafts to send")
    if body.mode == "now":
        argv = [PY, str(SCRIPTS / "send_pending.py")]
        if body.no_jitter:
            argv.append("--no-jitter")
        label = f"Send all {total_pending} pending drafts (now)"
    else:
        argv = [PY, str(SCRIPTS / "send_scheduler.py"), "--wait-and-send-pending"]
        if body.no_jitter:
            argv.append("--no-jitter")
        label = f"Send all {total_pending} pending drafts (scheduled)"
    return {"job_id": start_job(argv, label), "count": total_pending}


@app.post("/api/actions/clear-drafts")
def clear_drafts():
    """Delete all pending drafts from Outlook + reset DB state."""
    import win32com.client
    outlook = win32com.client.Dispatch("Outlook.Application")
    acc = None
    for a in outlook.Session.Accounts:
        if a.SmtpAddress.lower() == "pradip@bitcodingsolutions.com":
            acc = a; break
    if not acc:
        raise HTTPException(500, "pradip@ account not found in Outlook")
    folder = acc.DeliveryStore.GetDefaultFolder(16)
    deleted = 0
    for it in list(folder.Items):
        try:
            it.Delete(); deleted += 1
        except Exception:
            pass
    c = conn()
    try:
        reset_db = c.execute(
            "DELETE FROM emails_sent WHERE sent_at IS NULL"
        ).rowcount
        reset_status = c.execute(
            "UPDATE lead_status SET status='New', touch_count=0, first_sent_at=NULL, "
            "last_touch_date=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE status IN ('Picked','Drafted','DraftedInOutlook')"
        ).rowcount
        c.commit()
    finally:
        c.close()
    return {"deleted_outlook": deleted, "reset_db_rows": reset_db,
            "reset_lead_status": reset_status}


@app.get("/api/schedule")
def schedule_status():
    """Return current send-window status for Germany business hours."""
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Europe/Berlin')
    t = dt.datetime.now(TZ)
    allowed = {1, 2, 3}  # Tue/Wed/Thu
    in_window = (t.weekday() in allowed
                 and ((t.hour > 10) or (t.hour == 10 and t.minute >= 0))
                 and ((t.hour < 11) or (t.hour == 11 and t.minute < 30)))
    if in_window:
        end = t.replace(hour=11, minute=30, second=0, microsecond=0)
        return {
            "in_window": True,
            "now_local": t.isoformat(timespec='seconds'),
            "window_closes_at": end.isoformat(timespec='seconds'),
            "seconds_remaining": int((end - t).total_seconds()),
        }
    # next window
    cand = t.replace(hour=10, minute=0, second=0, microsecond=0)
    if t >= cand:
        cand += dt.timedelta(days=1)
    while cand.weekday() not in allowed:
        cand += dt.timedelta(days=1)
    return {
        "in_window": False,
        "now_local": t.isoformat(timespec='seconds'),
        "next_window_opens_at": cand.isoformat(timespec='seconds'),
        "seconds_until_open": int((cand - t).total_seconds()),
    }


class ScheduledSendBody(BaseModel):
    file: str
    count: int
    no_jitter: bool = False
    wait: bool = False  # if True, script blocks until window opens


@app.post("/api/actions/scheduled-send")
def scheduled_send(body: ScheduledSendBody):
    path = resolve_batch(body.file)
    flag = "--wait-and-send" if body.wait else "--send-if-window"
    argv = [PY, str(SCRIPTS / "send_scheduler.py"), flag,
            "--file", path, "--count", str(body.count)]
    if body.no_jitter:
        argv.append("--no-jitter")
    label = f"{'Wait+send' if body.wait else 'Send-if-window'} {body.count} from {body.file}"
    return {"job_id": start_job(argv, label)}


@app.post("/api/actions/sync-sent")
def sync_sent():
    argv = [PY, str(SCRIPTS / "mark_sent.py")]
    return {"job_id": start_job(argv, "Sync Outlook Sent folder")}


@app.post("/api/actions/scan-replies")
def scan_replies():
    argv = [PY, str(SCRIPTS / "scan_replies.py")]
    return {"job_id": start_job(argv, "Scan Outlook inbox for replies")}


@app.get("/api/batches/files")
def batch_files():
    if not BATCHES_DIR.exists():
        return []
    out = []
    for f in sorted(BATCHES_DIR.glob("*.xlsx"), reverse=True):
        out.append({
            "name": f.name,
            "size_kb": round(f.stat().st_size / 1024),
            "modified": dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
        })
    return out


@app.get("/api/batches/progress")
def batch_progress(file: str):
    """Per-batch counts from the xlsx + DB (DB is the source of truth)."""
    path = resolve_batch(file)
    import pandas as pd
    df = pd.read_excel(path)
    total = len(df)
    lead_ids = df['lead_id'].dropna().astype(str).tolist()
    if not lead_ids:
        return {"file": file, "total": total, "drafted": 0, "in_outlook": 0, "sent": 0,
                "pending_draft": total, "pending_outlook": 0, "pending_send": 0}
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


# ---- Job status ----
@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404)
    # Return a compact view, last 200 log lines
    return {**j, "logs": j.get("logs", [])[-200:]}


@app.get("/api/jobs")
def jobs():
    return sorted(JOBS.values(), key=lambda j: j.get("started_at", ""), reverse=True)[:30]


# ---- Reply actions ----
class HandleReplyBody(BaseModel):
    reply_id: int
    handled: bool = True


@app.post("/api/replies/handle")
def handle_reply(body: HandleReplyBody):
    c = conn()
    c.execute(
        "UPDATE replies SET handled=?, handled_at=CURRENT_TIMESTAMP WHERE id=?",
        (1 if body.handled else 0, body.reply_id),
    )
    c.commit()
    c.close()
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "db": DB, "time": dt.datetime.now().isoformat()}


BRIDGE_DIR = Path(r"H:/Lead Generator/Bridge")


def _ping_bridge(timeout: float = 1.5) -> bool:
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:8765/", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500  # any response means server is up
    except Exception:
        return False


@app.get("/api/bridge-health")
def bridge_health():
    """Ping the local Claude bridge (localhost:8765). Used for header indicator."""
    return {"ok": _ping_bridge()}


@app.post("/api/actions/start-bridge")
def start_bridge():
    """Launch the bridge in background via start-silent.vbs, then poll health."""
    import time
    if _ping_bridge():
        return {"started": False, "already_running": True, "ok": True}
    vbs = BRIDGE_DIR / "start-silent.vbs"
    if not vbs.exists():
        raise HTTPException(500, f"Bridge launcher not found: {vbs}")
    try:
        subprocess.Popen(
            ["wscript.exe", str(vbs)],
            cwd=str(BRIDGE_DIR),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to launch bridge: {e}")
    # Poll up to ~6s for the server to bind
    for _ in range(12):
        time.sleep(0.5)
        if _ping_bridge(timeout=1.0):
            return {"started": True, "already_running": False, "ok": True}
    return {"started": True, "already_running": False, "ok": False,
            "hint": "Launched but not responding yet; check Bridge/bridge.log"}
