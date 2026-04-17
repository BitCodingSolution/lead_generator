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
        "daily_quota": 25,
        "remaining_today": max(0, 25 - q_one(
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
