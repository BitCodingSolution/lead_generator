"""
LinkedIn Phase 7 endpoints — features ported from the legacy Apps Script
menu for full parity. Lives alongside linkedin_api.py so the base module
stays focused on the core flow.

Routes registered here:
    POST /api/linkedin/maintenance/reset-orphans
    POST /api/linkedin/maintenance/sweep-junk
    POST /api/linkedin/recyclebin/empty
    GET  /api/linkedin/autopilot/status

    GET  /api/linkedin/blocklist
    POST /api/linkedin/blocklist
    POST /api/linkedin/blocklist/{id}/delete

    GET  /api/linkedin/cvs
    POST /api/linkedin/cvs         (multipart upload)
    POST /api/linkedin/cvs/{id}/delete

    GET  /api/linkedin/followups
    POST /api/linkedin/followups/run
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from linkedin_db import DB_PATH, connect

router = APIRouter(prefix="/api/linkedin", tags=["linkedin-extras"])

import csv
import io

from fastapi.responses import PlainTextResponse

CV_CLUSTERS = ("python_ai", "fullstack", "scraping", "n8n", "default")
CV_STORAGE_DIR = DB_PATH.parent / "cvs"

# How long a lead can sit in Sending/Queued before we call it orphaned.
ORPHAN_AFTER_MINUTES = 10

# Follow-up cadence (days after the last outgoing touch).
FOLLOWUP_DAYS = (3, 7)


# ---------------------------------------------------------------- helpers


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _log(con, kind: str, lead_id: Optional[int] = None, meta: Optional[dict] = None):
    con.execute(
        "INSERT INTO events (at, kind, lead_id, meta_json) VALUES (?, ?, ?, ?)",
        (_now_iso(), kind, lead_id, json.dumps(meta) if meta else None),
    )


# -------------------------------------------------- maintenance actions


def reset_orphans() -> dict:
    """Any lead stuck in Sending or Queued beyond ORPHAN_AFTER_MINUTES — revert
    to Drafted so the next batch picks it up. Called on startup and manually."""
    cutoff = (dt.datetime.now() - dt.timedelta(minutes=ORPHAN_AFTER_MINUTES)).isoformat(
        timespec="seconds",
    )
    with connect() as con:
        cur = con.execute(
            "UPDATE leads SET status = 'Drafted' "
            "WHERE status IN ('Sending', 'Queued') "
            "  AND (sent_at IS NULL OR sent_at < ?) "
            "  AND (queued_at IS NULL OR queued_at < ?)",
            (cutoff, cutoff),
        )
        reset = cur.rowcount
        if reset:
            _log(con, "orphans_reset", meta={"count": reset})
        con.commit()
    return {"reset": reset}


@router.post("/maintenance/reset-orphans")
def api_reset_orphans():
    return reset_orphans()


@router.post("/maintenance/sweep-junk")
def api_sweep_junk():
    """Bulk-archive clearly-junk leads:
      • No email + no phone + no draft + older than 7 days.
      • Skipped leads regardless of age.
      • Leads where Claude already wrote skip_reason but status is still 'New'.
    """
    seven_days_ago = (dt.datetime.now() - dt.timedelta(days=7)).isoformat(
        timespec="seconds",
    )
    archived = 0
    with connect() as con:
        # Collect candidates first so _archive_lead can operate per-row.
        rows = con.execute(
            "SELECT id FROM leads WHERE ("
            "  (status = 'Skipped')"
            "  OR ((email IS NULL OR TRIM(email) = '') "
            "       AND (phone IS NULL OR TRIM(phone) = '') "
            "       AND (gen_subject IS NULL OR TRIM(gen_subject) = '') "
            "       AND last_seen_at < ?) "
            "  OR (skip_reason IS NOT NULL AND status = 'New')"
            ")",
            (seven_days_ago,),
        ).fetchall()
        for r in rows:
            _archive_lead_inline(con, r["id"], reason="swept_junk")
            archived += 1
        _log(con, "sweep_junk", meta={"count": archived})
        con.commit()
    return {"archived": archived}


def _archive_lead_inline(con, lead_id: int, reason: str) -> None:
    """Copy of _archive_lead from linkedin_api to avoid circular import."""
    row = con.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        return
    payload = {k: row[k] for k in row.keys()}
    con.execute(
        "INSERT OR REPLACE INTO recyclebin "
        "(original_id, post_url, payload_json, reason, moved_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (lead_id, row["post_url"], json.dumps(payload), reason, _now_iso()),
    )
    con.execute("DELETE FROM leads WHERE id = ?", (lead_id,))


@router.post("/recyclebin/empty")
def api_empty_recyclebin():
    with connect() as con:
        cur = con.execute("DELETE FROM recyclebin")
        deleted = cur.rowcount
        _log(con, "recyclebin_emptied", meta={"count": deleted})
        con.commit()
    return {"deleted": deleted}


# -------------------------------------------------- autopilot status


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


# -------------------------------------------------- blocklist


_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-.]*\.[a-z]{2,}$")


class BlocklistIn(BaseModel):
    kind: str = Field(pattern="^(company|domain)$")
    value: str = Field(min_length=2, max_length=120)
    reason: Optional[str] = Field(default=None, max_length=200)


@router.get("/blocklist")
def list_blocklist():
    with connect() as con:
        rows = con.execute(
            "SELECT id, kind, value, reason, created_at "
            "FROM blocklist ORDER BY created_at DESC"
        ).fetchall()
        return {"rows": [dict(r) for r in rows]}


@router.post("/blocklist")
def add_blocklist(payload: BlocklistIn):
    value = payload.value.strip().lower()
    if payload.kind == "domain":
        value = value.lstrip("@")
        if not _DOMAIN_RE.match(value):
            raise HTTPException(400, "Domain must look like example.com")

    with connect() as con:
        try:
            con.execute(
                "INSERT INTO blocklist (kind, value, reason, created_at) "
                "VALUES (?, ?, ?, ?)",
                (payload.kind, value, payload.reason, _now_iso()),
            )
        except Exception as e:
            if "UNIQUE" in str(e):
                raise HTTPException(409, f"{payload.kind} '{value}' already blocked")
            raise
        _log(con, "blocklist_add", meta=payload.model_dump() | {"value": value})
        con.commit()
    return {"ok": True}


@router.post("/blocklist/{item_id}/delete")
def del_blocklist(item_id: int):
    with connect() as con:
        cur = con.execute("DELETE FROM blocklist WHERE id = ?", (item_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Blocklist entry not found")
        _log(con, "blocklist_del", meta={"id": item_id})
        con.commit()
    return {"ok": True}


def is_blocked(company: Optional[str], email: Optional[str]) -> Optional[dict]:
    """Return {kind, value, reason} if blocked; None if clear."""
    comp = (company or "").strip().lower()
    mail = (email or "").strip().lower()
    domain = mail.split("@", 1)[1] if "@" in mail else ""
    if not (comp or domain):
        return None
    with connect() as con:
        rows = con.execute(
            "SELECT kind, value, reason FROM blocklist"
        ).fetchall()
    for r in rows:
        v = r["value"]
        if r["kind"] == "domain" and domain and (domain == v or domain.endswith("." + v)):
            return dict(r)
        if r["kind"] == "company" and comp and v in comp:
            return dict(r)
    return None


# -------------------------------------------------- CV library


class CVMeta(BaseModel):
    id: int
    cluster: str
    filename: str
    size_bytes: Optional[int]
    uploaded_at: str


@router.get("/cvs")
def list_cvs():
    with connect() as con:
        rows = con.execute(
            "SELECT id, cluster, filename, size_bytes, uploaded_at "
            "FROM cvs ORDER BY cluster"
        ).fetchall()
    configured = {r["cluster"] for r in rows}
    missing = [c for c in CV_CLUSTERS if c not in configured]
    return {
        "rows": [dict(r) for r in rows],
        "clusters": list(CV_CLUSTERS),
        "missing": missing,
    }


@router.post("/cvs")
async def upload_cv(cluster: str = Form(...), file: UploadFile = File(...)):
    if cluster not in CV_CLUSTERS:
        raise HTTPException(400, f"cluster must be one of {CV_CLUSTERS}")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "File must be a PDF")

    CV_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename)
    target = CV_STORAGE_DIR / f"{cluster}__{safe_name}"

    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    size = target.stat().st_size
    with connect() as con:
        # One CV per cluster — replace on re-upload.
        prev = con.execute(
            "SELECT stored_path FROM cvs WHERE cluster = ?", (cluster,)
        ).fetchone()
        if prev:
            try:
                Path(prev["stored_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            con.execute("DELETE FROM cvs WHERE cluster = ?", (cluster,))
        con.execute(
            "INSERT INTO cvs (cluster, filename, stored_path, size_bytes, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cluster, file.filename, str(target), size, _now_iso()),
        )
        _log(con, "cv_upload", meta={"cluster": cluster, "file": file.filename, "bytes": size})
        con.commit()

    return {"ok": True, "cluster": cluster, "filename": file.filename, "size_bytes": size}


@router.post("/cvs/{cv_id}/delete")
def delete_cv(cv_id: int):
    with connect() as con:
        row = con.execute(
            "SELECT stored_path FROM cvs WHERE id = ?", (cv_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "CV not found")
        try:
            Path(row["stored_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        con.execute("DELETE FROM cvs WHERE id = ?", (cv_id,))
        _log(con, "cv_delete", meta={"id": cv_id})
        con.commit()
    return {"ok": True}


def pick_cv_path(cluster: Optional[str]) -> Optional[tuple[Path, str]]:
    """Return (path, filename) for the CV matching this cluster, else the
    'default' CV, else None."""
    key = cluster if cluster in CV_CLUSTERS else "default"
    with connect() as con:
        row = con.execute(
            "SELECT stored_path, filename FROM cvs WHERE cluster = ?", (key,)
        ).fetchone()
        if row is None and key != "default":
            row = con.execute(
                "SELECT stored_path, filename FROM cvs WHERE cluster = 'default'"
            ).fetchone()
    if row is None:
        return None
    p = Path(row["stored_path"])
    return (p, row["filename"]) if p.exists() else None


# -------------------------------------------------- follow-ups


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


FOLLOWUP_TEMPLATE_1 = (
    "Hi {first_name},\n\n"
    "Circling back on my note from last week about {role_or_post}. "
    "Happy to share a quick sample of related work if useful, or jump on "
    "a 15-min call whenever suits.\n\n"
    "Best,\n"
    "Jaydip"
)

FOLLOWUP_TEMPLATE_2 = (
    "Hi {first_name},\n\n"
    "Last one from me. If the timing isn't right, no worries. Keeping the "
    "door open if you want to revisit the {role_or_post} work.\n\n"
    "Best,\n"
    "Jaydip"
)


def _first_name(raw: Optional[str]) -> str:
    if not raw:
        return "there"
    part = raw.strip().split()[0]
    return part if part else "there"


def _build_followup_body(sequence: int, posted_by: str, role: str) -> str:
    tmpl = FOLLOWUP_TEMPLATE_1 if sequence == 1 else FOLLOWUP_TEMPLATE_2
    return tmpl.format(
        first_name=_first_name(posted_by),
        role_or_post=(role or "role").strip() or "role",
    )


# -------------------------------------------------- analytics (day-by-day)


@router.get("/analytics")
def linkedin_analytics(days: int = 30):
    """Day-by-day counts of: drafted, sent, replied, bounced. Returns the
    last N days (default 30), oldest-first for chart rendering."""
    if days < 1 or days > 180:
        raise HTTPException(400, "days must be 1..180")

    end = dt.date.today()
    start = end - dt.timedelta(days=days - 1)

    # Bucket by day across leads table (sent_at, replied_at, bounced_at) and
    # events table (kind='draft').
    with connect() as con:
        def per_day(column: str, where_extra: str = "") -> dict[str, int]:
            rows = con.execute(
                f"SELECT DATE({column}) AS d, COUNT(*) AS n FROM leads "
                f"WHERE {column} IS NOT NULL AND DATE({column}) >= ? "
                f"      {('AND ' + where_extra) if where_extra else ''} "
                f"GROUP BY DATE({column})",
                (start.isoformat(),),
            ).fetchall()
            return {r["d"]: int(r["n"]) for r in rows}

        sent_map = per_day("sent_at")
        replied_map = per_day("replied_at")
        bounced_map = per_day("bounced_at")

        drafted_rows = con.execute(
            "SELECT DATE(at) AS d, COUNT(*) AS n FROM events "
            "WHERE kind = 'draft' AND DATE(at) >= ? "
            "GROUP BY DATE(at)",
            (start.isoformat(),),
        ).fetchall()
        drafted_map = {r["d"]: int(r["n"]) for r in drafted_rows}

        totals = {
            "total_leads": con.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
            "sent": con.execute(
                "SELECT COUNT(*) FROM leads WHERE sent_at IS NOT NULL"
            ).fetchone()[0],
            "replied": con.execute(
                "SELECT COUNT(*) FROM leads WHERE replied_at IS NOT NULL"
            ).fetchone()[0],
            "bounced": con.execute(
                "SELECT COUNT(*) FROM leads WHERE bounced_at IS NOT NULL"
            ).fetchone()[0],
            "recyclebin": con.execute("SELECT COUNT(*) FROM recyclebin").fetchone()[0],
        }

    series: list[dict] = []
    for i in range(days):
        d = (start + dt.timedelta(days=i)).isoformat()
        series.append({
            "day": d,
            "drafted": drafted_map.get(d, 0),
            "sent":    sent_map.get(d, 0),
            "replied": replied_map.get(d, 0),
            "bounced": bounced_map.get(d, 0),
        })

    reply_rate = (
        round(totals["replied"] / totals["sent"] * 100, 1)
        if totals["sent"]
        else 0.0
    )
    bounce_rate = (
        round(totals["bounced"] / totals["sent"] * 100, 1)
        if totals["sent"]
        else 0.0
    )

    return {
        "days": days,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "series": series,
        "totals": totals,
        "reply_rate_pct": reply_rate,
        "bounce_rate_pct": bounce_rate,
    }


# -------------------------------------------------- per-lead event timeline


@router.get("/leads/{lead_id:int}/events")
def lead_events(lead_id: int, limit: int = 100):
    with connect() as con:
        rows = con.execute(
            "SELECT id, at, kind, meta_json FROM events "
            "WHERE lead_id = ? ORDER BY at DESC LIMIT ?",
            (lead_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "at": r["at"],
                "kind": r["kind"],
                "meta": json.loads(r["meta_json"]) if r["meta_json"] else None,
            })
        return {"rows": out}


# -------------------------------------------------- CSV export


def _csv_response(filename: str, headers: list[str], rows: list[list]) -> PlainTextResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/leads/export")
def export_leads():
    cols = [
        "id", "post_url", "posted_by", "company", "role", "tech_stack",
        "location", "email", "phone", "status", "email_mode", "cv_cluster",
        "gen_subject", "jaydip_note", "first_seen_at", "sent_at",
        "replied_at", "bounced_at",
    ]
    with connect() as con:
        rows = con.execute(
            f"SELECT {', '.join(cols)} FROM leads ORDER BY first_seen_at DESC"
        ).fetchall()
    return _csv_response(
        f"linkedin_leads_{dt.date.today().isoformat()}.csv",
        cols,
        [[r[c] for c in cols] for r in rows],
    )


@router.get("/recyclebin/export")
def export_recyclebin():
    cols_out = [
        "id", "original_id", "post_url", "reason", "moved_at",
        "company", "posted_by", "role", "email",
    ]
    with connect() as con:
        rows = con.execute(
            "SELECT id, original_id, post_url, reason, moved_at, payload_json "
            "FROM recyclebin ORDER BY moved_at DESC"
        ).fetchall()

    data: list[list] = []
    for r in rows:
        p = json.loads(r["payload_json"] or "{}")
        data.append([
            r["id"], r["original_id"], r["post_url"], r["reason"], r["moved_at"],
            p.get("company"), p.get("posted_by"), p.get("role"), p.get("email"),
        ])
    return _csv_response(
        f"linkedin_recyclebin_{dt.date.today().isoformat()}.csv",
        cols_out,
        data,
    )


class FollowupRunIn(BaseModel):
    lead_ids: Optional[list[int]] = None        # empty → run all due
    dry_run: bool = False


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
        try:
            result = gmail.send_email(
                to=lead["email"], subject=subject, body=body,
            )
            with connect() as con:
                con.execute(
                    "INSERT INTO followups (lead_id, sequence, message_id, sent_at) "
                    "VALUES (?, ?, ?, ?)",
                    (lead["id"], seq, result.message_id, result.sent_at),
                )
                _record_send(con, lead["id"], result.message_id, result.sent_at)
                _log(con, "followup_send", lead_id=lead["id"],
                     meta={"sequence": seq, "msg_id": result.message_id})
                con.commit()
            sent += 1
        except Exception as e:
            with connect() as con:
                _record_failure(con, lead["id"], f"followup:{e}")
                con.commit()
            errors.append({"lead_id": lead["id"], "reason": str(e)[:200]})

    return {"sent": sent, "skipped": skipped, "errors": errors, "total": len(due)}
