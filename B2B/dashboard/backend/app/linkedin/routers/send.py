"""LinkedIn — send routes.

Carved from `app.linkedin.api`. Routes are byte-identical to the
original; the wildcard import below inherits every helper and
module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.api import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


@router.post("/send/lead/{lead_id}")
def send_one(lead_id: int):
    with connect() as con:
        lead = con.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        if lead is None:
            raise HTTPException(404, "Lead not found")
        if not lead["email"]:
            raise HTTPException(400, "Lead has no email address")
        if not (lead["gen_subject"] or "").strip() or not (lead["gen_body"] or "").strip():
            raise HTTPException(
                400, "Lead has no draft — generate one first"
            )
        if (lead["jaydip_note"] or "").strip():
            raise HTTPException(
                400, "Lead has a private note — remove it before sending"
            )
        if lead["status"] == "Sent":
            raise HTTPException(400, "Already sent")
        block = extras.is_blocked(lead["company"], lead["email"])
        if block:
            raise HTTPException(
                400,
                f"Blocked by {block['kind']} blocklist: {block['value']}",
            )
        _check_safety_before_send(con)
        missing_cv = extras.cv_required_but_missing(lead["cv_cluster"])
        if missing_cv:
            raise HTTPException(
                400,
                f"Missing CV for cluster '{missing_cv}'. Upload it in the "
                "CV library before sending this lead — a role-matched CV is "
                "required to attract this recipient.",
            )

    attachment = extras.pick_cv_path(lead["cv_cluster"])

    picked_account_id = gmail.pick_next_account_id()
    if picked_account_id is None:
        raise HTTPException(429, "No Gmail account with remaining quota")

    with connect() as con:
        token = _ensure_open_token(con, lead_id)
        con.commit()

    try:
        result = gmail.send_email(
            to=lead["email"],
            subject=lead["gen_subject"],
            body=lead["gen_body"],
            attachment=attachment,
            account_id=picked_account_id,
            tracking_pixel_url=_tracking_pixel_url(token),
        )
    except Exception as e:
        with connect() as con:
            _record_failure(con, lead_id, str(e))
            con.commit()
        gmail.record_send_failure(picked_account_id, str(e))
        raise HTTPException(502, f"Send failed: {e}")

    with connect() as con:
        _record_send(con, lead_id, result.message_id, result.sent_at,
                     account_id=result.account_id)
        con.commit()
    return {"sent_at": result.sent_at, "message_id": result.message_id,
            "account_id": result.account_id}


@router.post("/send/batch")
def send_batch(payload: BatchSendIn):
    global _batch_thread
    with _batch_lock:
        if _batch_state["running"]:
            raise HTTPException(409, "A batch is already running")
        # Defense in depth: state says idle but a prior thread might still
        # be unwinding after a crash. Block until the old thread is
        # actually dead rather than risk two workers mutating state.
        if _batch_thread is not None and _batch_thread.is_alive():
            _batch_stop_event.set()
            _batch_thread.join(timeout=5)
            if _batch_thread.is_alive():
                raise HTTPException(
                    503, "Previous batch worker still running — try again shortly",
                )
        _batch_stop_event.clear()

        with connect() as con:
            _check_safety_before_send(con)
            cap = _effective_daily_cap(con)
            remaining_quota = max(
                0,
                cap - con.execute(
                    "SELECT daily_sent_count FROM safety_state WHERE id=1"
                ).fetchone()[0],
            )
            take = min(payload.count, remaining_quota)
            if take <= 0:
                raise HTTPException(429, "Daily cap reached")
            lead_ids = _pick_ready_leads(con, take)
            if not lead_ids:
                raise HTTPException(400, "No ready leads to send")
            _log_event(con, "batch_start", meta={
                "source": payload.source, "count": len(lead_ids),
            })
            con.commit()

        _batch_state.update({
            "running": True,
            "total": len(lead_ids),
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "current_lead_id": None,
            "current_email": None,
            "last_error": None,
            "source": payload.source,
            "stop_requested": False,
        })
        _batch_thread = threading.Thread(
            target=_batch_worker,
            args=(lead_ids, payload.source),
            daemon=True,
            name="linkedin-batch-worker",
        )
        _batch_thread.start()

    return {"started": True, "total": len(lead_ids), "source": payload.source}


@router.get("/send/batch/status")
def batch_status():
    return dict(_batch_state)


@router.post("/send/batch/stop")
def batch_stop():
    if not _batch_state["running"]:
        return {"stopped": False, "message": "Not running"}
    _batch_state["stop_requested"] = True
    _batch_stop_event.set()
    return {"stopped": True}
