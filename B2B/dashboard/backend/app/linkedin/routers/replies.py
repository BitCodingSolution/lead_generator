"""LinkedIn — replies routes.

Carved from `app.linkedin.api`. Routes are byte-identical to the
original; the wildcard import below inherits every helper and
module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.api import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


@router.get("/leads/{lead_id:int}/replies")
def list_lead_replies(lead_id: int):
    """Return the full conversation thread for a lead — original outbound
    + every inbound reply + every outbound reply Jaydip has sent — merged
    chronologically so the drawer can render the back-and-forth as a real
    thread instead of stacking duplicate "Received reply (N)" boxes.

    Shape:
      - lead:    metadata (email, sent_message_id, received_on_email, ...)
      - replies: legacy inbound-only list (kept so older callers/tests
                 don't break — the drawer now consumes `thread` instead).
      - thread:  merged conversation — each entry has direction:
                   "out_initial"  the cold email we first sent
                   "in"           an inbound reply (real or auto)
                   "out_reply"    a reply Jaydip later sent in-thread
                 Sorted by `at` ascending so a forward render = oldest
                 message first (matches Gmail/most chat UIs).
    """
    with connect() as con:
        lead = con.execute(
            "SELECT id, email, posted_by, company, role, gen_subject, "
            "gen_body, sent_message_id, sent_at, sent_via_account_id "
            "FROM leads WHERE id = ?", (lead_id,),
        ).fetchone()
        if lead is None:
            raise HTTPException(404, "Lead not found")
        received_on = None
        if lead["sent_via_account_id"]:
            acct = con.execute(
                "SELECT email FROM gmail_accounts WHERE id = ?",
                (lead["sent_via_account_id"],),
            ).fetchone()
            received_on = acct["email"] if acct else None
        reps = con.execute(
            "SELECT id, gmail_msg_id, from_email, subject, snippet, body, "
            "received_at, kind, handled_at, sentiment, intent, "
            "auto_draft_body, auto_draft_at "
            "FROM replies WHERE lead_id = ? ORDER BY received_at ASC",
            (lead_id,),
        ).fetchall()
        # Outbound replies Jaydip has already sent in-thread — pulled
        # from the events log where send-reply persists `outbound_body`.
        sent_replies = con.execute(
            "SELECT at, meta_json FROM events "
            "WHERE lead_id = ? AND kind = 'reply_sent' "
            "ORDER BY at ASC",
            (lead_id,),
        ).fetchall()

    lead_out = dict(lead)
    lead_out["received_on_email"] = received_on

    # Build the merged thread.
    thread: list[dict] = []
    if lead["sent_at"] and lead["gen_body"]:
        thread.append({
            "direction": "out_initial",
            "at": lead["sent_at"],
            "subject": lead["gen_subject"],
            "body": lead["gen_body"],
        })
    for r in reps:
        thread.append({
            "direction": "in",
            "id": r["id"],
            "at": r["received_at"],
            "from_email": r["from_email"],
            "subject": r["subject"],
            "body": r["body"] or r["snippet"],
            "kind": r["kind"],
            "sentiment": r["sentiment"],
            "intent": (r["intent"] if "intent" in r.keys() else None),
            "handled_at": r["handled_at"],
            "auto_draft_body": r["auto_draft_body"],
            "auto_draft_at": r["auto_draft_at"],
        })
    for sr in sent_replies:
        try:
            meta = json.loads(sr["meta_json"] or "{}")
        except Exception:
            meta = {}
        body = meta.get("outbound_body") or ""
        if not body:
            continue
        thread.append({
            "direction": "out_reply",
            "at": sr["at"],
            "body": body,
        })
    thread.sort(key=lambda x: x.get("at") or "")

    return {
        "lead": lead_out,
        "replies": [dict(r) for r in reps],
        "thread": thread,
    }


@router.post("/leads/{lead_id:int}/draft-reply")
def draft_reply(lead_id: int, payload: Optional[DraftReplyBody] = None):
    """Ask the Bridge to draft a response to the lead's latest reply.
    Accepts an optional free-text `hint` — Claude incorporates it into
    the draft instead of producing a generic response."""
    hint = (payload.hint or "").strip() if payload else ""
    with connect() as con:
        lead = con.execute(
            "SELECT id, posted_by, gen_subject, gen_body "
            "FROM leads WHERE id = ?", (lead_id,),
        ).fetchone()
        if lead is None:
            raise HTTPException(404, "Lead not found")
        last = con.execute(
            "SELECT body, snippet FROM replies "
            "WHERE lead_id = ? AND kind = 'reply' "
            "ORDER BY received_at DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
        examples = _recent_style_examples(con, limit=5)
    if last is None:
        raise HTTPException(400, "No inbound reply to respond to")
    reply_text = (last["body"] or last["snippet"] or "").strip()
    first = _first_name_from_posted_by(lead["posted_by"] or "")
    draft, raw = linkedin_claude.generate_reply_draft(
        prospect_first_name=first,
        prospect_reply_text=reply_text,
        original_subject=lead["gen_subject"] or "",
        original_body=lead["gen_body"] or "",
        user_hint=hint,
        style_examples=examples,
    )
    if not draft:
        raise HTTPException(502, f"Bridge failed: {raw}")
    return {"body": draft, "used_hint": bool(hint), "style_examples_used": len(examples)}


@router.post("/leads/{lead_id:int}/send-reply")
def send_reply(lead_id: int, payload: SendReplyBody):
    """Send a threaded reply to the lead — uses the original Gmail
    thread's Message-ID as In-Reply-To so it nests in the same
    conversation on the recipient's side."""
    with connect() as con:
        lead = con.execute(
            "SELECT id, email, gen_subject, sent_message_id, sent_via_account_id "
            "FROM leads WHERE id = ?", (lead_id,),
        ).fetchone()
        if lead is None:
            raise HTTPException(404, "Lead not found")
        if not lead["email"]:
            raise HTTPException(400, "Lead has no email address")
        # Pick the latest inbound reply's Gmail message-id so our outbound
        # reply threads directly under it (not just the original outgoing).
        last_in = con.execute(
            "SELECT gmail_msg_id FROM replies "
            "WHERE lead_id = ? AND kind = 'reply' "
            "ORDER BY received_at DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
    latest_inbound_msgid = (last_in["gmail_msg_id"] if last_in else None)
    # Reference chain: original sent msgid + latest inbound so full thread
    # context is preserved.
    refs_parts: list[str] = []
    if lead["sent_message_id"]:
        refs_parts.append(f"<{lead['sent_message_id']}>")
    if latest_inbound_msgid:
        refs_parts.append(f"<{latest_inbound_msgid}>")
    references = " ".join(refs_parts) if refs_parts else None
    in_reply_to = latest_inbound_msgid or lead["sent_message_id"]

    subject = (payload.subject or "").strip() or f"Re: {lead['gen_subject'] or ''}".strip()
    # Prefer the same account that sent the original, else pick any active.
    account_id = lead["sent_via_account_id"] or gmail.pick_next_account_id()
    if account_id is None:
        raise HTTPException(429, "No Gmail account with remaining quota")

    try:
        result = gmail.send_email(
            to=lead["email"],
            subject=subject,
            body=payload.body,
            account_id=account_id,
            in_reply_to=in_reply_to,
            references=references,
        )
    except Exception as e:
        raise HTTPException(500, f"Send failed: {e}")

    now = dt.datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        # Pull the inbound that Jaydip is responding to — we persist it
        # alongside his outbound body so the drafter can feed past pairs
        # as few-shot examples on future replies.
        last_inbound = con.execute(
            "SELECT body, snippet FROM replies "
            "WHERE lead_id = ? AND kind = 'reply' "
            "ORDER BY received_at DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
        inbound_text = ""
        if last_inbound:
            inbound_text = (last_inbound["body"] or last_inbound["snippet"] or "").strip()

        # Mark the last inbound reply as handled.
        con.execute(
            "UPDATE replies SET handled_at = ? "
            "WHERE lead_id = ? AND kind = 'reply' AND handled_at IS NULL",
            (now, lead_id),
        )
        # Stamp replied_at / needs_attention=0 (user has now actioned it).
        con.execute(
            "UPDATE leads SET needs_attention = 0 WHERE id = ?", (lead_id,),
        )
        _log_event(
            con, "reply_sent", lead_id=lead_id,
            meta={
                "account_id": account_id,
                "msg_id": result.message_id,
                "chars": len(payload.body),
                # Store both sides trimmed — _recent_style_examples reads this
                # to feed Claude few-shot style guidance on future drafts.
                "inbound_snippet": inbound_text[:500],
                "outbound_body": payload.body[:1500],
            },
        )
        con.commit()
    return {"ok": True, "message_id": result.message_id, "sent_at": result.sent_at}


@router.post("/replies/{reply_id:int}/handled")
def mark_reply_handled(reply_id: int, payload: MarkHandledBody):
    now = dt.datetime.now().isoformat(timespec="seconds") if payload.handled else None
    with connect() as con:
        cur = con.execute(
            "UPDATE replies SET handled_at = ? WHERE id = ?",
            (now, reply_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Reply not found")
        con.commit()
    return {"ok": True, "handled": payload.handled}


@router.post("/replies/bulk-handle")
def bulk_handle_replies(payload: BulkHandleBody):
    """Mark many replies handled/unhandled in one shot. The UI checkbox
    flow sends both real reply ids (positive) and synthetic manual-tag
    ids (negative, = -lead_id). We route them to the right table."""
    now = dt.datetime.now().isoformat(timespec="seconds") if payload.handled else None
    real_ids = [i for i in payload.reply_ids if i > 0]
    manual_lead_ids = [-i for i in payload.reply_ids if i < 0]

    affected = 0
    with connect() as con:
        if real_ids:
            placeholders = ",".join(["?"] * len(real_ids))
            cur = con.execute(
                f"UPDATE replies SET handled_at = ? WHERE id IN ({placeholders})",
                [now] + real_ids,
            )
            affected += cur.rowcount
        if manual_lead_ids:
            # For manual-tagged leads "handled" == needs_attention=0; unhandle
            # flips it back to 1 so they resurface for triage.
            placeholders = ",".join(["?"] * len(manual_lead_ids))
            new_na = 0 if payload.handled else 1
            cur = con.execute(
                f"UPDATE leads SET needs_attention = ?, reviewed_at = "
                f"CASE WHEN ? IS NULL THEN NULL ELSE COALESCE(reviewed_at, ?) END "
                f"WHERE id IN ({placeholders})",
                [new_na, now, now] + manual_lead_ids,
            )
            affected += cur.rowcount
        con.commit()
    return {"ok": True, "affected": affected, "handled": payload.handled}


@router.get("/replies")
def list_replies(
    limit: int = Query(100, ge=1, le=500),
    handled: Optional[bool] = Query(None),
    sentiment: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    include_manual: bool = Query(False),
):
    """Inbound reply feed.

    `include_manual=true` additionally surfaces leads whose status was
    flipped to Replied via a call signal or a private note (no real email
    came in). These are shown as synthetic rows with source='manual' so
    the UI can render them alongside actual email replies — handy for an
    "all Replied leads" view where both off-email signals and real
    inbound mail live in one place."""
    clauses: list[str] = []
    params: list = []
    if kind:
        clauses.append("r.kind = ?")
        params.append(kind)
    if handled is not None:
        clauses.append("r.handled_at IS NOT NULL" if handled else "r.handled_at IS NULL")
    if sentiment:
        if sentiment == "none":
            clauses.append("r.sentiment IS NULL")
        else:
            clauses.append("r.sentiment = ?")
            params.append(sentiment)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as con:
        real_rows = con.execute(
            f"SELECT r.id, r.lead_id, r.from_email, r.subject, r.snippet, "
            f"  r.received_at, r.kind, r.sentiment, r.handled_at, "
            f"  r.auto_draft_body, r.auto_draft_at, "
            f"  l.company, l.posted_by, l.role, l.gen_subject, l.status, "
            f"  l.call_status, l.open_count, l.email AS lead_email "
            f"FROM replies r LEFT JOIN leads l ON l.id = r.lead_id "
            f"{where} ORDER BY r.received_at DESC LIMIT ?",
            tuple(params) + (limit,),
        ).fetchall()
        rows = [dict(r) | {"source": "email"} for r in real_rows]

        if include_manual and (kind in (None, "", "reply")):
            # Leads marked Replied without an inbound email row — surface
            # them so Overview counters and Inbox totals reconcile.
            manual_clauses = [
                "l.status = 'Replied'",
                "NOT EXISTS (SELECT 1 FROM replies r WHERE r.lead_id = l.id AND r.kind = 'reply')",
            ]
            # Sentiment filter for manual rows — derive from call_status.
            call_map = {
                "positive": "green", "question": "yellow",
                "not_interested": "red",
            }
            if sentiment:
                if sentiment == "none":
                    manual_clauses.append(
                        "(l.call_status IS NULL OR TRIM(l.call_status) = '')"
                    )
                elif sentiment in call_map:
                    manual_clauses.append("l.call_status = ?")
                    params_manual = [call_map[sentiment]]
                else:
                    # ooo / referral aren't representable via call_status;
                    # skip manual rows for those filters.
                    manual_clauses.append("1 = 0")
                    params_manual = []
            else:
                params_manual = []
            if handled is not None:
                manual_clauses.append(
                    "l.needs_attention = 0" if handled else "l.needs_attention = 1"
                )
            manual_where = " AND ".join(manual_clauses)
            manual_rows = con.execute(
                f"SELECT l.id AS lead_id, l.email AS from_email, "
                f"  l.gen_subject AS subject, "
                f"  COALESCE(l.jaydip_note, '') AS snippet, "
                f"  l.replied_at AS received_at, l.call_status, "
                f"  l.company, l.posted_by, l.role, l.gen_subject, l.status, "
                f"  l.open_count, l.email AS lead_email, l.reviewed_at "
                f"FROM leads l WHERE {manual_where} "
                f"ORDER BY l.replied_at DESC LIMIT ?",
                tuple(params_manual) + (limit,),
            ).fetchall()
            # Map call_status to an equivalent sentiment bucket so the
            # existing UI filter + badge code works unchanged.
            cs_to_sent = {
                "green": "positive", "yellow": "question", "red": "not_interested",
            }
            for r in manual_rows:
                d = dict(r)
                d["id"] = -d["lead_id"]   # synthetic negative id — never collides with real reply ids
                d["kind"] = "reply"
                d["sentiment"] = cs_to_sent.get((d.get("call_status") or "").lower())
                d["handled_at"] = d.pop("reviewed_at")
                d["source"] = "manual"
                rows.append(d)

        rows.sort(key=lambda r: r.get("received_at") or "", reverse=True)
        return {"rows": rows[:limit]}


@router.post("/replies/poll")
def poll_replies():
    try:
        return _poll_and_store()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Poll failed: {e}")
