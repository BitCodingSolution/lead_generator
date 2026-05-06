"""LinkedIn — leads routes.

Carved from `app.linkedin.api`. Routes are byte-identical to the
original; the wildcard import below inherits every helper and
module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.api import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


@router.get("/leads")
def list_leads(
    status: Optional[str] = Query(None),
    needs_attention: Optional[bool] = Query(None),
    call_status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    sort: str = Query("recent"),    # recent | score
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    clauses: list[str] = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if needs_attention is not None:
        clauses.append("needs_attention = ?")
        params.append(1 if needs_attention else 0)
    if call_status:
        cs = call_status.strip().lower()
        if cs == "none":
            clauses.append("(call_status IS NULL OR TRIM(call_status) = '')")
        elif cs in ("green", "yellow", "red"):
            clauses.append("call_status = ?")
            params.append(cs)
        elif cs == "any":
            clauses.append("call_status IS NOT NULL AND TRIM(call_status) != ''")
    if q:
        clauses.append(
            "(company LIKE ? OR posted_by LIKE ? OR role LIKE ? OR email LIKE ? "
            "OR location LIKE ? OR tech_stack LIKE ? OR post_text LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like] * 7)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    # Sort resolution. Two legacy tokens kept working exactly as before:
    #   - "recent": last_seen_at DESC (default)
    #   - "score":  fit_score DESC, last_seen_at DESC
    # Plus column-based tokens for click-to-sort headers:
    #   "{col}_asc" / "{col}_desc" where col is one of the whitelist below.
    # Unknown tokens fall back to "recent" so a stale URL never 500s.
    SORT_COLUMN_MAP = {
        "fit":        "COALESCE(fit_score, -1)",
        "company":    "LOWER(COALESCE(company, ''))",
        "posted_by":  "LOWER(COALESCE(posted_by, ''))",
        "role":       "LOWER(COALESCE(role, ''))",
        "email":      "LOWER(COALESCE(email, ''))",
        "phone":      "COALESCE(phone, '')",
        "status":     "status",
        "call":       "COALESCE(call_status, '')",
        "first_seen": "first_seen_at",
        "last_seen":  "last_seen_at",
    }
    if sort == "score":
        order_sql = "ORDER BY COALESCE(fit_score, -1) DESC, first_seen_at DESC"
    elif "_" in sort and sort.rsplit("_", 1)[0] in SORT_COLUMN_MAP \
         and sort.rsplit("_", 1)[1] in ("asc", "desc"):
        col_key, direction = sort.rsplit("_", 1)
        expr = SORT_COLUMN_MAP[col_key]
        # Tiebreak on id so the order is stable across paginated fetches
        # even when many rows share the same sort key value.
        order_sql = f"ORDER BY {expr} {direction.upper()}, id DESC"
    else:
        # "recent" — order by when we first saw the lead (true creation),
        # not last_seen_at (which bumps on every re-scan and shuffles old
        # leads to the top whenever the extension re-pings them).
        order_sql = "ORDER BY first_seen_at DESC, id DESC"

    with connect() as con:
        # Lazy snooze sweep — any lead whose remind_at has passed gets
        # remind_at cleared and needs_attention forced back on, so it
        # naturally resurfaces in the inbox / leads list. Cheap: indexed
        # column, runs once per /leads fetch instead of needing a cron.
        now_iso = dt.datetime.now().isoformat(timespec="seconds")
        con.execute(
            "UPDATE ln_leads SET needs_attention = 1, remind_at = NULL "
            "WHERE remind_at IS NOT NULL AND remind_at <= ?",
            (now_iso,),
        )
        con.commit()

        total = con.execute(
            f"SELECT COUNT(*) FROM ln_leads {where}", tuple(params)
        ).fetchone()[0]
        rows = con.execute(
            f"SELECT id, post_url, posted_by, company, role, tech_stack, location, "
            f"email, phone, status, gen_subject, cv_cluster, first_seen_at, last_seen_at, "
            f"sent_at, replied_at, needs_attention, call_status, reviewed_at, "
            f"jaydip_note, open_count, first_opened_at, last_opened_at, "
            f"scheduled_send_at, ooo_nudge_at, ooo_nudge_sent_at, "
            f"fit_score, fit_score_reasons, remind_at "
            f"FROM ln_leads {where} {order_sql} LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        ).fetchall()
        # Compute which CV clusters are currently uploaded so the UI can
        # flag leads whose matched specialty slot is empty BEFORE the user
        # clicks Send (which would 400 on cv_required_but_missing). One
        # roundtrip covers the whole page.
        present_clusters = {
            r[0] for r in con.execute("SELECT cluster FROM ln_cvs").fetchall()
        }

        # Recruiter-spam signal: same posted_by name appearing across
        # 3+ distinct companies in the last 30d typically means a
        # third-party recruiter spraying job posts. We compute it once
        # for the whole page (single GROUP BY) and look it up per row.
        cutoff_30d = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        recruiter_names = {
            r["posted_by"]
            for r in con.execute(
                "SELECT posted_by, COUNT(DISTINCT company) AS n_companies "
                "FROM ln_leads "
                "WHERE posted_by IS NOT NULL AND TRIM(posted_by) != '' "
                "  AND DATE(first_seen_at) >= ? "
                "GROUP BY posted_by HAVING COUNT(DISTINCT company) >= 3",
                (cutoff_30d,),
            ).fetchall()
        }
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        cluster = d.get("cv_cluster")
        # Legacy alias — treat these as ai_llm for UI purposes.
        effective = "ai_llm" if cluster in ("python_ai", "ai_ml") else cluster
        # Only flag actionable rows: Drafted/New with a specialty cluster
        # whose slot is empty. Sent/Replied are water under the bridge.
        d["cv_missing"] = bool(
            effective
            and effective != "default"
            and effective not in present_clusters
            and d.get("status") in ("New", "Drafted")
        )
        d["is_recruiter"] = bool(
            d.get("posted_by") and d["posted_by"] in recruiter_names
        )
        d["temperature"] = _lead_temperature(d)
        out.append(d)
    return {"rows": out, "total": total}


@router.get("/leads/export.csv")
def export_leads_csv(
    status: Optional[str] = Query(None),
    needs_attention: Optional[bool] = Query(None),
    call_status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    """Stream the current leads view as CSV. Mirrors the same filters
    the /leads endpoint accepts so the file always matches what the
    user sees on the dashboard. No paging — exports the full filtered
    set so reports stay reproducible."""
    import csv as _csv
    from io import StringIO as _SIO
    clauses: list[str] = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if needs_attention is not None:
        clauses.append("needs_attention = ?")
        params.append(1 if needs_attention else 0)
    if call_status:
        cs = call_status.strip().lower()
        if cs == "none":
            clauses.append("(call_status IS NULL OR TRIM(call_status) = '')")
        elif cs in ("green", "yellow", "red"):
            clauses.append("call_status = ?")
            params.append(cs)
        elif cs == "any":
            clauses.append("call_status IS NOT NULL AND TRIM(call_status) != ''")
    if q:
        clauses.append(
            "(company LIKE ? OR posted_by LIKE ? OR role LIKE ? OR email LIKE ? "
            "OR location LIKE ? OR tech_stack LIKE ? OR post_text LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like] * 7)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    cols = [
        "id", "post_url", "posted_by", "company", "role", "tech_stack",
        "location", "email", "phone", "status", "fit_score", "cv_cluster",
        "first_seen_at", "sent_at", "replied_at", "bounced_at",
        "call_status", "open_count", "jaydip_note",
    ]

    def _gen():
        buf = _SIO()
        w = _csv.writer(buf)
        w.writerow(cols)
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
        with connect() as con:
            for r in con.execute(
                f"SELECT {', '.join(cols)} FROM ln_leads {where} "
                f"ORDER BY first_seen_at DESC, id DESC",
                tuple(params),
            ):
                w.writerow([r[c] if r[c] is not None else "" for c in cols])
                yield buf.getvalue()
                buf.seek(0); buf.truncate(0)

    today = dt.date.today().isoformat()
    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="linkedin_leads_{today}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/leads/rescore-all")
def rescore_all_leads():
    """One-shot: rescore every lead with current heuristics. Useful
    after adjusting weights or on first-time upgrade from a pre-scoring
    install. Scales to ~thousands in <1s."""
    with connect() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM ln_leads").fetchall()]
        for lead_id in ids:
            _rescore(con, lead_id)
        con.commit()
    return {"ok": True, "rescored": len(ids)}


@router.get("/leads/{lead_id:int}")
def get_lead(lead_id: int):
    with connect() as con:
        r = con.execute("SELECT * FROM ln_leads WHERE id = ?", (lead_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "Lead not found")
        return dict(r)


@router.post("/leads/{lead_id:int}")
def patch_lead(lead_id: int, patch: LeadPatch):
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True, "updated": 0}
    if "needs_attention" in updates:
        updates["needs_attention"] = 1 if updates["needs_attention"] else 0
    if "call_status" in updates:
        cs = str(updates["call_status"] or "").strip().lower()
        if cs not in ("", "green", "yellow", "red"):
            raise HTTPException(400, "call_status must be green/yellow/red/empty")
        updates["call_status"] = cs or None
    if "email" in updates:
        em = str(updates["email"] or "").strip()
        if em:
            # Cheap structural check — defends against typos like
            # "name@domain..com" that fail at SMTP time. We don't try to
            # be RFC-perfect here; the SMTP layer will reject anything we
            # let through that's still bad.
            if (em.count("@") != 1
                or ".." in em
                or em.startswith(".") or em.endswith(".")
                or " " in em
                or "@." in em or ".@" in em):
                raise HTTPException(400, f"Invalid email format: {em}")
        updates["email"] = em or None
    if "phone" in updates:
        ph = str(updates["phone"] or "").strip()
        updates["phone"] = ph or None

    # Any non-empty note or call_status counts as "user reviewed this lead".
    # Stamp reviewed_at once (first time) so the UI can dim reviewed rows.
    note_val = (updates.get("jaydip_note") or "").strip()
    call_val = updates.get("call_status")
    marks_reviewed = bool(note_val or call_val)

    auto_archived = False
    auto_replied = False
    with connect() as con:
        row = con.execute(
            "SELECT id, reviewed_at, status, replied_at FROM ln_leads WHERE id = ?",
            (lead_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Lead not found")
        now = dt.datetime.now().isoformat(timespec="seconds")
        if marks_reviewed and not row["reviewed_at"]:
            updates["reviewed_at"] = now

        # A note or call signal on a Sent lead = user had real-world contact
        # (reply, phone call, DM). Promote to Replied so it shows up in the
        # Sent & Replies tab + overview counts, and flag for attention.
        if marks_reviewed and row["status"] == "Sent":
            updates["status"] = "Replied"
            updates["needs_attention"] = 1
            if not row["replied_at"]:
                updates["replied_at"] = now
            auto_replied = True

        sets = ", ".join(f"{k} = ?" for k in updates)
        con.execute(f"UPDATE ln_leads SET {sets} WHERE id = ?", [*updates.values(), lead_id])
        if auto_replied:
            _log_event(con, "manual_reply", lead_id=lead_id,
                       meta={"source": "call_status_or_note",
                             "call_status": updates.get("call_status"),
                             "note": (updates.get("jaydip_note") or "")[:120]})
        # If a draft was edited, ensure status reflects Drafted at minimum.
        if "gen_subject" in updates or "gen_body" in updates:
            con.execute(
                "UPDATE ln_leads SET status = 'Drafted' "
                "WHERE id = ? AND status IN ('New', 'Skipped')",
                (lead_id,),
            )
            # Draft content changed — rescore (gen_subject/body adds +15).
            _rescore(con, lead_id)
        # Rejection-note auto-move (matches legacy Apps Script behaviour).
        note = updates.get("jaydip_note")
        if note and REJECTION_NOTE_RE.search(note):
            _archive_lead(con, lead_id, reason="user_note")
            auto_archived = True
        con.commit()

    return {
        "ok": True,
        "updated": len(updates),
        "auto_archived": auto_archived,
        "auto_replied": auto_replied,
    }


@router.post("/leads/{lead_id}/archive")
def archive_lead(lead_id: int, payload: ArchiveRequest):
    with connect() as con:
        _archive_lead(con, lead_id, payload.reason)
        con.commit()
    return {"archived": lead_id, "reason": payload.reason}


@router.post("/leads/bulk-archive")
def bulk_archive_leads(payload: BulkLeadIdsBody):
    """Move N leads to the recyclebin in a single transaction. Silently
    skips IDs that don't exist so a partial selection doesn't 404 the
    whole call."""
    reason = payload.reason or "bulk"
    archived = 0
    with connect() as con:
        for lid in payload.ids:
            try:
                _archive_lead(con, lid, reason)
                archived += 1
            except HTTPException:
                continue
        con.commit()
    return {"archived": archived, "requested": len(payload.ids)}


@router.post("/leads/bulk-snooze")
def bulk_snooze_leads(payload: BulkSnoozeBody):
    raw = payload.remind_at.strip()
    if len(raw) <= 5 and raw[-1] in ("h", "d", "w") and raw[:-1].isdigit():
        n = int(raw[:-1])
        delta = {"h": dt.timedelta(hours=n),
                 "d": dt.timedelta(days=n),
                 "w": dt.timedelta(weeks=n)}[raw[-1]]
        parsed = dt.datetime.now() + delta
    else:
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "remind_at must be ISO-8601 or '<n>h|d|w'")
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
    if parsed <= dt.datetime.now():
        raise HTTPException(400, "remind_at must be in the future")
    when = parsed.isoformat(timespec="seconds")
    with connect() as con:
        placeholders = ",".join("?" * len(payload.ids))
        cur = con.execute(
            f"UPDATE ln_leads SET remind_at = ?, needs_attention = 0 "
            f"WHERE id IN ({placeholders})",
            (when, *payload.ids),
        )
        _log_event(con, "bulk_snooze",
                   meta={"count": cur.rowcount, "remind_at": when})
        con.commit()
    return {"snoozed": cur.rowcount, "remind_at": when}


@router.post("/leads/{lead_id}/restore")
def restore_lead(lead_id: int):
    """Restore from ln_recyclebin. `lead_id` here is the recyclebin row id."""
    with connect() as con:
        r = con.execute(
            "SELECT * FROM ln_recyclebin WHERE id = ?", (lead_id,)
        ).fetchone()
        if r is None:
            raise HTTPException(404, "Recyclebin row not found")
        data = json.loads(r["payload_json"])
        cols = [
            "post_url", "posted_by", "company", "role", "tech_stack", "rate",
            "location", "tags", "post_text", "email", "phone", "status",
            "gen_subject", "gen_body", "email_mode", "cv_cluster",
            "jaydip_note", "skip_reason", "skip_source",
            "first_seen_at", "last_seen_at", "queued_at", "sent_at",
            "replied_at", "bounced_at", "follow_up_at", "needs_attention",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        values = [data.get(c) for c in cols]
        con.execute(
            f"INSERT INTO ln_leads ({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )
        new_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("DELETE FROM ln_recyclebin WHERE id = ?", (lead_id,))
        # If this post_url was previously "cleared" to the shadow table,
        # drop that entry too — the user has changed their mind.
        post_url = data.get("post_url")
        if post_url:
            con.execute(
                "DELETE FROM ln_archived_urls WHERE post_url = ?", (post_url,)
            )
        _log_event(con, "restore", lead_id=new_id)
        con.commit()
    return {"restored": new_id}


@router.get("/recyclebin")
def list_recyclebin(limit: int = 200):
    with connect() as con:
        rows = con.execute(
            "SELECT id, original_id, post_url, reason, moved_at, payload_json "
            "FROM ln_recyclebin ORDER BY moved_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            p = json.loads(r["payload_json"])
            out.append({
                "id": r["id"],
                "original_id": r["original_id"],
                "post_url": r["post_url"],
                "reason": r["reason"],
                "moved_at": r["moved_at"],
                "company": p.get("company"),
                "posted_by": p.get("posted_by"),
                "role": p.get("role"),
                "email": p.get("email"),
            })
        return {"rows": out}


@router.post("/leads/{lead_id:int}/schedule")
def schedule_send(lead_id: int, payload: ScheduleBody):
    """Schedule a drafted lead to send at a specific ISO timestamp. The
    scheduler loop checks every 60s; actual send goes through send_one
    which re-runs safety, warmup, and blocklist checks at that moment."""
    try:
        parsed = dt.datetime.fromisoformat(payload.scheduled_send_at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "scheduled_send_at must be ISO-8601")
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()   # assume local TZ if bare
    when = parsed.isoformat(timespec="seconds")
    with connect() as con:
        row = con.execute(
            "SELECT id, email, status FROM ln_leads WHERE id = ?", (lead_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Lead not found")
        if not row["email"]:
            raise HTTPException(400, "Lead has no email address")
        if row["status"] not in ("New", "Drafted", "Skipped"):
            raise HTTPException(
                400, f"Cannot schedule a lead in status '{row['status']}'"
            )
        con.execute(
            "UPDATE ln_leads SET scheduled_send_at = ?, "
            "status = CASE WHEN status = 'New' THEN 'Drafted' ELSE status END "
            "WHERE id = ?",
            (when, lead_id),
        )
        _log_event(con, "scheduled", lead_id=lead_id,
                   meta={"send_at": when})
        con.commit()
    return {"ok": True, "scheduled_send_at": when}


@router.post("/leads/{lead_id:int}/unschedule")
def unschedule_send(lead_id: int):
    with connect() as con:
        cur = con.execute(
            "UPDATE ln_leads SET scheduled_send_at = NULL "
            "WHERE id = ? AND scheduled_send_at IS NOT NULL",
            (lead_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Lead has no schedule")
        _log_event(con, "unscheduled", lead_id=lead_id)
        con.commit()
    return {"ok": True}


@router.post("/leads/{lead_id:int}/snooze")
def snooze_lead(lead_id: int, payload: SnoozeBody):
    """Hide a lead from the 'needs attention' queue until remind_at.
    Accepts either ISO-8601 or a relative token: '1d', '3d', '1w', '2h'.
    Lazy sweep in /leads clears remind_at and re-flags needs_attention
    once the timestamp passes."""
    raw = payload.remind_at.strip()
    parsed: dt.datetime | None = None
    # Relative: <n><unit>  where unit in h/d/w
    if len(raw) <= 5 and raw[-1] in ("h", "d", "w") and raw[:-1].isdigit():
        n = int(raw[:-1])
        delta = {"h": dt.timedelta(hours=n),
                 "d": dt.timedelta(days=n),
                 "w": dt.timedelta(weeks=n)}[raw[-1]]
        parsed = dt.datetime.now() + delta
    else:
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "remind_at must be ISO-8601 or '<n>h|d|w'")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    if parsed <= dt.datetime.now():
        raise HTTPException(400, "remind_at must be in the future")
    when = parsed.isoformat(timespec="seconds")
    with connect() as con:
        row = con.execute(
            "SELECT id FROM ln_leads WHERE id = ?", (lead_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Lead not found")
        con.execute(
            "UPDATE ln_leads SET remind_at = ?, needs_attention = 0 WHERE id = ?",
            (when, lead_id),
        )
        _log_event(con, "snoozed", lead_id=lead_id, meta={"remind_at": when})
        con.commit()
    return {"ok": True, "remind_at": when}


@router.post("/leads/{lead_id:int}/unsnooze")
def unsnooze_lead(lead_id: int):
    with connect() as con:
        cur = con.execute(
            "UPDATE ln_leads SET remind_at = NULL, needs_attention = 1 "
            "WHERE id = ? AND remind_at IS NOT NULL",
            (lead_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Lead is not snoozed")
        _log_event(con, "unsnoozed", lead_id=lead_id)
        con.commit()
    return {"ok": True}
