"""LinkedIn — followups routes.

Carved from `app.linkedin.extras`. Routes are byte-identical to
the original; the wildcard import below inherits every helper
and module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.extras import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin-extras"])


@router.get("/autopilot/status")
def autopilot_status():
    with connect() as con:
        s = con.execute(
            "SELECT autopilot_enabled, autopilot_hour FROM safety_state WHERE id=1"
        ).fetchone()
        last = con.execute(
            "SELECT fired_at, fired_date, total_queued, status "
            "FROM autopilot_runs ORDER BY fired_at DESC LIMIT 1"
        ).fetchone()
    enabled = bool(s and s["autopilot_enabled"])
    hour = int(s["autopilot_hour"]) if s else 10

    # Compute next fire: today at hour if still ahead, else tomorrow.
    now = dt.datetime.now()
    today_target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    last_date = last["fired_date"] if last else None
    if last_date == now.date().isoformat():
        next_fire = today_target + dt.timedelta(days=1)
    elif now >= today_target:
        next_fire = today_target + dt.timedelta(days=1)
    else:
        next_fire = today_target

    return {
        "enabled": enabled,
        "hour": hour,
        "last_fired_at": last["fired_at"] if last else None,
        "last_fired_date": last_date,
        "last_queued": last["total_queued"] if last else None,
        "last_status": last["status"] if last else None,
        "next_fire_at": next_fire.isoformat(timespec="seconds") if enabled else None,
    }


@router.get("/followups")
def list_due_followups(window_days: int = 14):
    """Leads that are Sent, have no reply, and are due for a follow-up."""
    now = dt.datetime.now()
    cutoff = (now - dt.timedelta(days=window_days)).isoformat(timespec="seconds")
    with connect() as con:
        rows = con.execute(
            """
            SELECT l.id, l.company, l.posted_by, l.email, l.gen_subject,
                   l.sent_at, l.status,
                   (SELECT MAX(sent_at) FROM followups f WHERE f.lead_id = l.id) AS last_followup_at,
                   (SELECT COUNT(*) FROM followups f WHERE f.lead_id = l.id) AS followup_count
            FROM leads l
            WHERE l.status = 'Sent'
              AND l.sent_at IS NOT NULL
              AND l.sent_at >= ?
              AND l.replied_at IS NULL
              AND l.bounced_at IS NULL
              AND (l.jaydip_note IS NULL OR TRIM(l.jaydip_note) = '')
            ORDER BY l.sent_at ASC
            """,
            (cutoff,),
        ).fetchall()

    due: list[dict] = []
    for r in rows:
        last_touch = r["last_followup_at"] or r["sent_at"]
        delta = now - dt.datetime.fromisoformat(last_touch)
        count = int(r["followup_count"])
        if count >= len(FOLLOWUP_DAYS):
            continue
        required = FOLLOWUP_DAYS[count]
        if delta.days < required:
            continue
        d = dict(r)
        d["next_sequence"] = count + 1
        d["days_since_last_touch"] = delta.days
        due.append(d)
    return {"rows": due, "cadence": list(FOLLOWUP_DAYS)}


@router.post("/digest/run")
def run_digest(force: bool = False):
    """Build and send Jaydip's daily morning digest. Triggered by the
    9am scheduler tick (idempotent — only fires once per day) but also
    callable manually with ?force=1 for testing or a re-send.

    Body summarises the last 24h of activity:
      - sent / replies / bounces / new leads
      - top fit-score Drafted lead currently waiting
      - any pending replies still un-handled

    Recipient comes from env var LINKEDIN_DIGEST_RECIPIENT, falling back
    to the email of the first connected Gmail account so the default
    Just Works after the first connect. Returns 503 if Gmail isn't
    configured yet — the scheduler interprets that as "skip silently"
    so a missing inbox doesn't spam errors."""
    import os as _os
    from linkedin_api import _digest_already_sent, _mark_digest_sent  # type: ignore  # noqa: WPS433
    today = _now_iso()[:10]
    if not force and _digest_already_sent(today):
        return {"sent": False, "reason": "already_sent_today"}

    # Pick recipient: env override, else first Gmail account's email.
    recipient = _os.environ.get("LINKEDIN_DIGEST_RECIPIENT", "").strip()
    if not recipient:
        with connect() as con:
            row = con.execute(
                "SELECT email FROM gmail_accounts WHERE status = 'active' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
            recipient = row["email"] if row else ""
    if not recipient:
        raise HTTPException(503, "No recipient configured (no Gmail accounts)")

    # Build digest body.
    yesterday = (dt.datetime.now() - dt.timedelta(days=1)).date().isoformat()
    today_d = dt.date.today().isoformat()
    with connect() as con:
        def _cnt(sql: str, params: tuple = ()) -> int:
            row = con.execute(sql, params).fetchone()
            return int(row[0]) if row else 0
        sent_24h = _cnt(
            "SELECT COUNT(*) FROM leads WHERE DATE(sent_at) >= ?", (yesterday,))
        replied_24h = _cnt(
            "SELECT COUNT(*) FROM leads WHERE DATE(replied_at) >= ?", (yesterday,))
        bounced_24h = _cnt(
            "SELECT COUNT(*) FROM leads WHERE DATE(bounced_at) >= ?", (yesterday,))
        new_24h = _cnt(
            "SELECT COUNT(*) FROM leads WHERE DATE(first_seen_at) >= ?", (yesterday,))
        pending_replies = _cnt(
            "SELECT COUNT(DISTINCT lead_id) FROM replies "
            "WHERE kind = 'reply' AND handled_at IS NULL")
        top_lead = con.execute(
            "SELECT id, posted_by, company, role, fit_score "
            "FROM leads WHERE status = 'Drafted' AND email IS NOT NULL "
            "ORDER BY COALESCE(fit_score, -1) DESC, first_seen_at DESC LIMIT 1"
        ).fetchone()

    lines: list[str] = []
    lines.append(f"Daily LinkedIn outreach digest - {today_d}")
    lines.append("")
    lines.append("Last 24h:")
    lines.append(f"  Sent:    {sent_24h}")
    lines.append(f"  Replies: {replied_24h}")
    lines.append(f"  Bounces: {bounced_24h}")
    lines.append(f"  New leads ingested: {new_24h}")
    lines.append("")
    lines.append(f"Pending action: {pending_replies} repl{'y' if pending_replies == 1 else 'ies'} awaiting your response.")
    lines.append("")
    if top_lead:
        lines.append("Top Drafted lead waiting:")
        lines.append(
            f"  {top_lead['posted_by'] or '?'} at {top_lead['company'] or '?'} "
            f"({top_lead['role'] or '?'}) - fit {top_lead['fit_score'] or '-'}"
        )
        lines.append("  Open: https://b2b.bitcodingsolutions.com/linkedin/leads")
        lines.append("")
    lines.append("Open dashboard: https://b2b.bitcodingsolutions.com/linkedin")
    body = "\n".join(lines)

    subject = (
        f"[Outreach] {sent_24h} sent / {replied_24h} replied / "
        f"{pending_replies} pending - {today_d}"
    )

    from linkedin_gmail import send_email as _send  # noqa: WPS433
    try:
        _send(to=recipient, subject=subject, body=body)
    except Exception as e:
        raise HTTPException(502, f"Digest send failed: {e}")
    _mark_digest_sent(today)
    return {"sent": True, "to": recipient, "subject": subject}


@router.post("/followups/run")
def run_followups(payload: FollowupRunIn):
    """Send pending follow-ups. Safety rails (quota, quiet hours, pause) still
    apply since we route through linkedin_gmail.send_email()."""
    import linkedin_gmail as gmail
    from linkedin_api import _check_safety_before_send, _record_send, _record_failure

    # Build the due queue (respect specific IDs if given).
    due = list_due_followups()["rows"]
    if payload.lead_ids:
        wanted = set(payload.lead_ids)
        due = [d for d in due if d["id"] in wanted]
    if payload.dry_run:
        return {"dry_run": True, "would_send": len(due), "leads": due}

    if gmail.get_credentials() is None:
        raise HTTPException(400, "Gmail not connected")

    sent = 0
    skipped = 0
    errors: list[dict] = []

    with connect() as con:
        try:
            _check_safety_before_send(con)
        except HTTPException as e:
            return {"sent": 0, "skipped": len(due), "blocked_by_safety": str(e.detail)}

    for lead in due:
        # Respect blocklist at send time too.
        blocked = is_blocked(lead.get("company"), lead.get("email"))
        if blocked:
            skipped += 1
            errors.append({"lead_id": lead["id"], "reason": f"blocked:{blocked['kind']}"})
            continue

        seq = int(lead["next_sequence"])
        body = _build_followup_body(seq, lead.get("posted_by") or "", "")
        # Prefix subject with Re: to thread on recipient side.
        subject = f"Re: {lead.get('gen_subject') or 'Following up'}"
        picked_account_id = gmail.pick_next_account_id()
        if picked_account_id is None:
            errors.append({"lead_id": lead["id"],
                           "reason": "No Gmail account with remaining quota"})
            continue

        # Re-use the lead's original open_token so follow-up opens roll up
        # into the same lead row; generate one if the lead predates tracking.
        import secrets as _sec
        with connect() as con:
            r = con.execute(
                "SELECT open_token FROM leads WHERE id = ?", (lead["id"],),
            ).fetchone()
            token = (r["open_token"] if r and r["open_token"] else _sec.token_urlsafe(22))
            if not (r and r["open_token"]):
                con.execute(
                    "UPDATE leads SET open_token = ? WHERE id = ?",
                    (token, lead["id"]),
                )
                con.commit()
        # Reuse the public-host guard from linkedin_api so a localhost /
        # RFC1918 base URL produces no pixel (same behaviour as the primary
        # send path). Avoids shipping a broken <img> tag in follow-ups.
        from linkedin_api import _tracking_pixel_url as _tp  # noqa: WPS433
        pixel_url = _tp(token)

        # Follow-ups are intentionally text-only — a second-touch with the
        # same CV re-attached looks spammy, and most recipients already
        # have the initial attachment in the threaded conversation.
        #
        # If you ever want to attach a *different* CV on follow-ups (e.g. a
        # shorter one-pager), uncomment the block below. `pick_cv_path`
        # already honours the per-cluster slot, and `cv_required_but_missing`
        # gives you the same strict stall-on-empty behaviour the initial
        # send has.
        #
        #   missing = cv_required_but_missing(lead.get("cv_cluster"))
        #   if missing:
        #       errors.append({"lead_id": lead["id"],
        #                      "reason": f"cv_missing:{missing}"})
        #       continue
        #   fu_attachment = pick_cv_path(lead.get("cv_cluster"))
        # and then pass attachment=fu_attachment into send_email below.
        try:
            result = gmail.send_email(
                to=lead["email"], subject=subject, body=body,
                account_id=picked_account_id,
                tracking_pixel_url=pixel_url,
            )
            with connect() as con:
                con.execute(
                    "INSERT INTO followups (lead_id, sequence, message_id, sent_at) "
                    "VALUES (?, ?, ?, ?)",
                    (lead["id"], seq, result.message_id, result.sent_at),
                )
                _record_send(con, lead["id"], result.message_id, result.sent_at,
                             account_id=result.account_id)
                _log(con, "followup_send", lead_id=lead["id"],
                     meta={"sequence": seq, "msg_id": result.message_id})
                con.commit()
            sent += 1
        except Exception as e:
            with connect() as con:
                _record_failure(con, lead["id"], f"followup:{e}")
                con.commit()
            try:
                gmail.record_send_failure(picked_account_id, str(e))
            except Exception:
                pass
            errors.append({"lead_id": lead["id"], "reason": str(e)[:200]})

    return {"sent": sent, "skipped": skipped, "errors": errors, "total": len(due)}
