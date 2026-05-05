"""Marcel-pipeline action endpoints (`/api/actions/*`)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.db import conn, q_all, q_one
from app.schemas.actions import (
    BatchFileBody,
    FollowupBody,
    PickBody,
    RunPipelineBody,
    ScheduledSendBody,
    SendAllDraftsBody,
    SendBody,
)
from app.services.batch_export import resolve_marcel_batch
from app.services.jobs import pipeline_running, start_job
from app.services.preflight import preflight_report

router = APIRouter(prefix="/api", tags=["actions"])

PY = lambda: settings.python_executable  # noqa: E731  (small helper)


@router.post("/actions/pick-batch")
def pick_batch(body: PickBody) -> dict:
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "pick_batch.py"),
        "--industry", body.industry, "--count", str(body.count),
    ]
    if body.tier:
        argv += ["--tier", str(body.tier)]
    if body.city:
        argv += ["--city", body.city]
    job_id = start_job(argv, f"Pick {body.count} from {body.industry}")
    return {"job_id": job_id}


@router.post("/actions/generate-drafts")
def generate_drafts(body: BatchFileBody) -> dict:
    path = resolve_marcel_batch(body.file)
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "generate_drafts.py"),
        "--file", path,
    ]
    if body.limit:
        argv += ["--limit", str(body.limit)]
    return {"job_id": start_job(argv, f"Generate drafts: {body.file}")}


@router.post("/actions/write-outlook")
def write_outlook(body: BatchFileBody) -> dict:
    path = resolve_marcel_batch(body.file)
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "write_to_outlook.py"),
        "--file", path,
    ]
    return {"job_id": start_job(argv, f"Write to Outlook: {body.file}")}


@router.post("/actions/backup-db")
def backup_db() -> dict:
    argv = [settings.python_executable, str(settings.scripts_dir / "backup_db.py")]
    return {"job_id": start_job(argv, "Backup leads.db")}


@router.get("/actions/preflight")
def preflight() -> dict:
    return preflight_report()


@router.post("/actions/run-pipeline")
def run_pipeline(body: RunPipelineBody) -> dict:
    """Orchestrate the whole flow in one job: pick -> generate -> Outlook -> send."""
    if body.count <= 0:
        raise HTTPException(400, "count must be > 0")
    if pipeline_running():
        raise HTTPException(409, "Another pipeline is already running")
    pf = preflight_report()
    if not pf["ok"]:
        reasons = [c["error"] for c in pf["checks"] if not c["ok"] and c.get("error")]
        raise HTTPException(
            503,
            "Pre-flight failed: " + "; ".join(reasons) if reasons else "Pre-flight failed",
        )
    if body.send_mode == "now":
        today = dt.date.today().isoformat()
        sent_today = q_one(
            "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today
        )
        remaining = max(0, settings.daily_quota - sent_today)
        if body.count > remaining:
            raise HTTPException(
                400,
                f"Daily quota exceeded: {body.count} requested, {remaining} left today",
            )
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "run_pipeline.py"),
        "--industry", body.industry,
        "--count", str(body.count),
        "--send-mode", body.send_mode,
    ]
    if body.tier:
        argv += ["--tier", str(body.tier)]
    if body.no_jitter:
        argv.append("--no-jitter")
    label = f"Pipeline: {body.industry} x {body.count} ({body.send_mode})"
    return {"job_id": start_job(argv, label)}


@router.post("/actions/generate-and-push")
def generate_and_push(body: BatchFileBody) -> dict:
    path = resolve_marcel_batch(body.file)
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "generate_and_push.py"),
        "--file", path,
    ]
    if body.limit:
        argv += ["--limit", str(body.limit)]
    return {"job_id": start_job(argv, f"Generate+push: {body.file}")}


@router.post("/actions/send-drafts")
def send_drafts(body: SendBody) -> dict:
    path = resolve_marcel_batch(body.file)
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "send_drafts.py"),
        "--file", path, "--count", str(body.count),
    ]
    if body.no_jitter:
        argv.append("--no-jitter")
    return {"job_id": start_job(argv, f"Send {body.count} from {body.file}")}


@router.post("/actions/queue-followups")
def queue_followups(body: FollowupBody) -> dict:
    if body.touch not in (2, 3):
        raise HTTPException(400, "touch must be 2 or 3")
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "queue_followups.py"),
        "--touch", str(body.touch),
        "--days", str(body.days),
        "--count", str(body.count),
    ]
    return {
        "job_id": start_job(
            argv, f"Queue touch-{body.touch} follow-ups (Day-{body.days})"
        )
    }


@router.get("/pending-drafts")
def pending_drafts() -> dict:
    rows = q_all("""
        SELECT e.id, e.lead_id, e.subject, l.name, l.company, l.email,
               l.industry, l.city, e.batch_date
        FROM emails_sent e
        JOIN leads l ON e.lead_id = l.lead_id
        WHERE e.sent_at IS NULL AND e.outlook_entry_id IS NOT NULL
        ORDER BY e.id DESC
    """)
    return {"count": len(rows), "items": rows}


@router.post("/actions/send-all-drafts")
def send_all_drafts(body: SendAllDraftsBody) -> dict:
    """Send every pending draft in Outlook, regardless of source batch file."""
    total_pending = q_one(
        "SELECT COUNT(*) FROM emails_sent WHERE sent_at IS NULL AND outlook_entry_id IS NOT NULL"
    )
    if not total_pending:
        raise HTTPException(400, "No pending drafts to send")
    if body.mode == "now":
        argv = [settings.python_executable, str(settings.scripts_dir / "send_pending.py")]
        if body.no_jitter:
            argv.append("--no-jitter")
        label = f"Send all {total_pending} pending drafts (now)"
    else:
        argv = [
            settings.python_executable,
            str(settings.scripts_dir / "send_scheduler.py"),
            "--wait-and-send-pending",
        ]
        if body.no_jitter:
            argv.append("--no-jitter")
        label = f"Send all {total_pending} pending drafts (scheduled)"
    return {"job_id": start_job(argv, label), "count": total_pending}


@router.post("/actions/clear-drafts")
def clear_drafts() -> dict:
    """Delete only pipeline-owned pending drafts from Outlook + reset DB state."""
    import win32com.client  # type: ignore

    outlook = win32com.client.Dispatch("Outlook.Application")
    acc = None
    for a in outlook.Session.Accounts:
        if a.SmtpAddress.lower() == settings.outlook_account.lower():
            acc = a
            break
    if not acc:
        raise HTTPException(500, f"{settings.outlook_account} not found in Outlook")

    pending = q_all(
        "SELECT id, lead_id, outlook_entry_id FROM emails_sent "
        "WHERE sent_at IS NULL AND outlook_entry_id IS NOT NULL AND outlook_entry_id != ''"
    )
    ns = outlook.GetNamespace("MAPI")

    deleted = 0
    missing = 0
    for row in pending:
        eid = row["outlook_entry_id"]
        try:
            item = ns.GetItemFromID(eid)
            if not getattr(item, "Sent", True):
                item.Delete()
                deleted += 1
            else:
                missing += 1
        except Exception:
            missing += 1

    c = conn()
    try:
        ids = [row["id"] for row in pending]
        if ids:
            ph = ",".join("?" * len(ids))
            reset_db = c.execute(
                f"DELETE FROM emails_sent WHERE id IN ({ph})", ids
            ).rowcount
        else:
            reset_db = 0
        lead_ids = [row["lead_id"] for row in pending]
        if lead_ids:
            ph = ",".join("?" * len(lead_ids))
            reset_status = c.execute(
                f"UPDATE lead_status SET status='New', touch_count=0, "
                f"first_sent_at=NULL, last_touch_date=NULL, "
                f"updated_at=CURRENT_TIMESTAMP "
                f"WHERE lead_id IN ({ph}) AND status IN "
                f"('Picked','Drafted','DraftedInOutlook')",
                lead_ids,
            ).rowcount
        else:
            reset_status = 0
        c.commit()
    finally:
        c.close()
    return {
        "deleted_outlook": deleted,
        "missing_in_outlook": missing,
        "reset_db_rows": reset_db,
        "reset_lead_status": reset_status,
    }


@router.get("/schedule")
def schedule_status() -> dict:
    """Send-window status for Germany business hours."""
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Europe/Berlin')
    t = dt.datetime.now(TZ)
    allowed = {1, 2, 3}  # Tue/Wed/Thu
    in_window = (
        t.weekday() in allowed
        and ((t.hour > 10) or (t.hour == 10 and t.minute >= 0))
        and ((t.hour < 11) or (t.hour == 11 and t.minute < 30))
    )
    if in_window:
        end = t.replace(hour=11, minute=30, second=0, microsecond=0)
        return {
            "in_window": True,
            "now_local": t.isoformat(timespec='seconds'),
            "window_closes_at": end.isoformat(timespec='seconds'),
            "seconds_remaining": int((end - t).total_seconds()),
        }
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


@router.post("/actions/scheduled-send")
def scheduled_send(body: ScheduledSendBody) -> dict:
    path = resolve_marcel_batch(body.file)
    flag = "--wait-and-send" if body.wait else "--send-if-window"
    argv = [
        settings.python_executable,
        str(settings.scripts_dir / "send_scheduler.py"),
        flag, "--file", path, "--count", str(body.count),
    ]
    if body.no_jitter:
        argv.append("--no-jitter")
    label = (
        f"{'Wait+send' if body.wait else 'Send-if-window'} "
        f"{body.count} from {body.file}"
    )
    return {"job_id": start_job(argv, label)}


@router.post("/actions/sync-sent")
def sync_sent() -> dict:
    argv = [settings.python_executable, str(settings.scripts_dir / "mark_sent.py")]
    return {"job_id": start_job(argv, "Sync Outlook Sent folder")}


@router.post("/actions/scan-replies")
def scan_replies() -> dict:
    argv = [settings.python_executable, str(settings.scripts_dir / "scan_replies.py")]
    return {"job_id": start_job(argv, "Scan Outlook inbox for replies")}
