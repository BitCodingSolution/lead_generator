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

CV_CLUSTERS = ("python", "ml", "ai_llm", "fullstack", "scraping", "n8n", "default")
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


@router.post("/recyclebin/clear")
def api_clear_recyclebin():
    """Free the recyclebin but remember which post_urls were rejected so
    they can't be re-ingested. Large payload_json rows go, a lightweight
    (post_url, reason) shadow row moves to archived_urls."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        moved = con.execute(
            "INSERT OR IGNORE INTO archived_urls (post_url, reason, archived_at) "
            "SELECT post_url, reason, ? FROM recyclebin "
            "WHERE post_url IS NOT NULL AND post_url != ''",
            (now,),
        ).rowcount
        cur = con.execute("DELETE FROM recyclebin")
        deleted = cur.rowcount
        _log(con, "recyclebin_cleared", meta={"deleted": deleted, "shadowed": moved})
        con.commit()
    return {"deleted": deleted, "shadowed": moved}


@router.post("/recyclebin/purge")
def api_purge_recyclebin():
    """Fully forget. Deletes recyclebin AND archived_urls shadow rows, so
    previously-rejected posts can re-ingest as fresh leads."""
    with connect() as con:
        cur = con.execute("DELETE FROM recyclebin")
        deleted = cur.rowcount
        cur2 = con.execute("DELETE FROM archived_urls")
        forgotten = cur2.rowcount
        _log(con, "recyclebin_purged",
             meta={"deleted": deleted, "forgotten": forgotten})
        con.commit()
    return {"deleted": deleted, "forgotten": forgotten}


# Legacy endpoint kept for old clients / existing docs. Behaves like clear
# (dedup-preserving) — the safer default.
@router.post("/recyclebin/empty")
def api_empty_recyclebin():
    return api_clear_recyclebin()


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
_EMAIL_RE  = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def _archive_matching_leads(con, kind: str, value: str, reason: str) -> int:
    """Move leads already in the DB that match a newly-added blocklist
    entry into recyclebin. Prevents them from appearing in Drafted queues
    after the block rule is added. Returns count archived.

    Only touches leads that haven't been Sent yet — already-sent leads are
    left as historical record."""
    value = value.lower()
    if kind == "email":
        rows = con.execute(
            "SELECT id FROM leads "
            "WHERE LOWER(TRIM(email)) = ? AND status != 'Sent'",
            (value,),
        ).fetchall()
    elif kind == "domain":
        # Match emails ending in @<domain> OR @sub.<domain>.
        rows = con.execute(
            "SELECT id FROM leads WHERE status != 'Sent' AND ("
            "  LOWER(email) LIKE ? OR LOWER(email) LIKE ?"
            ")",
            (f"%@{value}", f"%.{value}"),
        ).fetchall()
    elif kind == "company":
        rows = con.execute(
            "SELECT id FROM leads "
            "WHERE status != 'Sent' AND LOWER(COALESCE(company, '')) LIKE ?",
            (f"%{value}%",),
        ).fetchall()
    else:
        return 0

    archived = 0
    for r in rows:
        lead_id = r["id"]
        row = con.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            continue
        con.execute(
            "INSERT OR REPLACE INTO recyclebin "
            "(original_id, post_url, payload_json, reason, moved_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                lead_id, row["post_url"],
                json.dumps({k: row[k] for k in row.keys()}),
                f"blocklist:{kind}:{value} ({reason})",
                _now_iso(),
            ),
        )
        con.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        archived += 1
    return archived


class BlocklistIn(BaseModel):
    kind: str = Field(pattern="^(company|domain|email)$")
    value: str = Field(min_length=2, max_length=200)
    reason: Optional[str] = Field(default=None, max_length=200)


class BlocklistBulkIn(BaseModel):
    # Paste a newline- or comma-separated list of emails (or domains).
    # kind is auto-inferred per entry: contains '@' -> email; else domain.
    text: str = Field(min_length=1, max_length=50_000)
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
    elif payload.kind == "email":
        if not _EMAIL_RE.match(value):
            raise HTTPException(400, "Must be a valid email address")

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
        archived = _archive_matching_leads(con, payload.kind, value,
                                           reason=payload.reason or "blocklist")
        con.commit()
    return {"ok": True, "archived_existing": archived}


@router.post("/blocklist/bulk")
def bulk_add_blocklist(payload: BlocklistBulkIn):
    """Paste a big list of emails / domains. Each non-empty token is
    inferred (contains '@' → email; else → domain) and inserted. Duplicates
    skipped silently. Existing matching leads are auto-archived to
    recyclebin so they drop out of the Drafted queue."""
    raw = payload.text.replace(",", "\n").replace(";", "\n")
    tokens = [t.strip().lower() for t in raw.splitlines() if t.strip()]

    added = {"email": 0, "domain": 0}
    skipped = 0
    invalid = []
    archived_total = 0

    with connect() as con:
        for tok in tokens:
            if "@" in tok:
                if not _EMAIL_RE.match(tok):
                    invalid.append(tok)
                    continue
                kind = "email"
            else:
                dom = tok.lstrip("@")
                if not _DOMAIN_RE.match(dom):
                    invalid.append(tok)
                    continue
                kind, tok = "domain", dom
            try:
                con.execute(
                    "INSERT INTO blocklist (kind, value, reason, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (kind, tok, payload.reason, _now_iso()),
                )
                added[kind] += 1
                archived_total += _archive_matching_leads(
                    con, kind, tok, reason=payload.reason or "blocklist_bulk"
                )
            except Exception as e:
                if "UNIQUE" in str(e):
                    skipped += 1
                else:
                    raise
        _log(con, "blocklist_bulk_add", meta={
            "added": added, "skipped_duplicates": skipped,
            "invalid": len(invalid), "archived_existing": archived_total,
        })
        con.commit()

    return {
        "ok": True,
        "added": added,
        "skipped_duplicates": skipped,
        "invalid": invalid[:20],   # first 20 for debugging
        "archived_existing": archived_total,
    }


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
    """Return {kind, value, reason} if blocked; None if clear.
    Checks three kinds: exact-email match, domain (incl. subdomain match),
    and company (substring match in the lead's company name)."""
    comp = (company or "").strip().lower()
    mail = (email or "").strip().lower()
    domain = mail.split("@", 1)[1] if "@" in mail else ""
    if not (comp or domain or mail):
        return None
    with connect() as con:
        rows = con.execute(
            "SELECT kind, value, reason FROM blocklist"
        ).fetchall()
    for r in rows:
        v = r["value"]
        if r["kind"] == "email" and mail and mail == v:
            return dict(r)
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
    """Return (path, filename) for the CV matching this cluster, else None.

    Policy: the whole reason we classify a role into a specialty cluster is
    to hook the recipient with a role-matched CV. A generic Master has weak
    pull, so we refuse to silently substitute one — if the specialty slot
    is empty the caller MUST stall the send (see cv_required_but_missing).

    The `default` slot is only attached when classification itself couldn't
    pick a specialty (cluster is None or literally 'default'), meaning the
    post was too generic for a targeted CV anyway.

    Legacy aliases: old 'python_ai' → 'ai_llm'; old 'ai_ml' → 'ai_llm'
    (the LLM/agent bucket is closer to what python_ai fired on). These are
    safety nets; proper migration re-classifies leads against new keywords."""
    if cluster in ("python_ai", "ai_ml"):
        cluster = "ai_llm"
    key = cluster if cluster in CV_CLUSTERS else "default"
    with connect() as con:
        row = con.execute(
            "SELECT stored_path, filename FROM cvs WHERE cluster = ?", (key,)
        ).fetchone()
        # Only fall back to default when the caller explicitly had no
        # specialty (cluster was unknown → key == 'default' already) OR
        # asked for default. For a known specialty slot that's empty, we
        # return None so the send path can hold the lead.
        if row is None and key == "default":
            row = None  # no default uploaded either — truly nothing to attach
    if row is None:
        return None
    p = Path(row["stored_path"])
    return (p, row["filename"]) if p.exists() else None


def cv_required_but_missing(cluster: Optional[str]) -> Optional[str]:
    """Return the specialty cluster name whose CV slot is empty but required
    for this lead, else None. Used by the send path to stall leads that
    would otherwise ship without a role-matched attachment."""
    c = "ai_llm" if cluster in ("python_ai", "ai_ml") else cluster
    if c is None or c == "default" or c not in CV_CLUSTERS:
        return None  # generic — default slot (if any) is fine
    with connect() as con:
        row = con.execute(
            "SELECT 1 FROM cvs WHERE cluster = ?", (c,)
        ).fetchone()
    return None if row else c


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
        lines.append(f"  Open: https://b2b.bitcodingsolutions.com/linkedin/leads")
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


@router.get("/dns/check")
def dns_check(domain: str):
    """Best-effort SPF / DKIM / DMARC health check for a sending domain.
    Lightweight — no auth because the data is read-only and public.
    Returns per-record: present bool, value string (truncated), and a
    simple verdict (ok / missing / soft). DKIM lookup is a shallow probe
    of common selectors since the real selector depends on the provider
    (Microsoft uses 'selector1' / 'selector2', Google uses 'google')."""
    import re as _re
    domain = (domain or "").strip().lower().strip(".")
    if not _re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        raise HTTPException(400, "Invalid domain")
    try:
        import dns.resolver  # type: ignore
    except Exception:
        raise HTTPException(500, "dnspython not installed on server")

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 4.0
    resolver.timeout = 2.0

    def _txt(name: str) -> list[str]:
        try:
            ans = resolver.resolve(name, "TXT")
            out: list[str] = []
            for rr in ans:
                # Each TXT rdata is a tuple of byte chunks. Join them.
                chunks = [
                    c.decode("utf-8", "replace") if isinstance(c, (bytes, bytearray)) else str(c)
                    for c in getattr(rr, "strings", [])
                ]
                out.append("".join(chunks) if chunks else str(rr).strip('"'))
            return out
        except Exception:
            return []

    def _first(records: list[str], prefix: str) -> str | None:
        for r in records:
            if r.lower().startswith(prefix):
                return r
        return None

    root = _txt(domain)
    spf_val = _first(root, "v=spf1")
    spf_verdict = (
        "ok" if spf_val and (" -all" in spf_val or " ~all" in spf_val) else
        "soft" if spf_val else "missing"
    )

    dmarc = _first(_txt(f"_dmarc.{domain}"), "v=dmarc1")
    dmarc_verdict = (
        "ok" if dmarc and "p=reject" in dmarc.lower() else
        "soft" if dmarc and "p=quarantine" in dmarc.lower() else
        "soft" if dmarc else "missing"
    )

    # Probe common DKIM selectors. Stop at the first hit; report it.
    dkim_selector = None
    dkim_val = None
    for sel in ("selector1", "selector2", "google", "default", "s1", "s2", "k1"):
        vals = _txt(f"{sel}._domainkey.{domain}")
        if vals:
            dkim_selector = sel
            dkim_val = vals[0]
            break
    dkim_verdict = "ok" if dkim_val else "missing"

    def _trim(v: str | None) -> str | None:
        if not v:
            return v
        return v if len(v) <= 220 else v[:217] + "..."

    return {
        "domain": domain,
        "spf":   {"verdict": spf_verdict,   "value": _trim(spf_val)},
        "dkim":  {"verdict": dkim_verdict,  "value": _trim(dkim_val),
                  "selector": dkim_selector},
        "dmarc": {"verdict": dmarc_verdict, "value": _trim(dmarc)},
    }


@router.get("/outreach-stats")
def outreach_stats():
    """Reply-rate breakdown by style signals, to answer the 'which
    approaches get replies?' question. Groups sent leads by:
      - cv_cluster (which CV / pitch specialty)
      - body_length_bucket (<60 / 60-120 / 120+ words)
      - subject_prefix (first word of gen_subject, lowercased)
      - weekday (Mon-Sun of sent_at)

    For each bucket returns sent / replied / positive counts plus
    percentages. Small table, recomputed on-demand — no caching. Use
    this to spot which buckets outperform the average."""
    def bucket_len(body: str | None) -> str:
        if not body:
            return "unknown"
        words = len(body.split())
        if words < 60:
            return "short (<60w)"
        if words < 120:
            return "medium (60-120w)"
        return "long (120+w)"

    def subject_prefix(subj: str | None) -> str:
        if not subj:
            return "(none)"
        first = subj.strip().split(" ", 1)[0].lower().strip(",.:;?!")
        return first[:20] if first else "(empty)"

    def weekday(iso: str | None) -> str:
        if not iso:
            return "unknown"
        try:
            return dt.datetime.fromisoformat(iso).strftime("%a")
        except ValueError:
            return "unknown"

    with connect() as con:
        rows = con.execute(
            "SELECT l.id, l.gen_subject, l.gen_body, l.cv_cluster, l.sent_at, "
            "       l.replied_at, "
            "       (SELECT sentiment FROM replies WHERE lead_id = l.id "
            "        ORDER BY id DESC LIMIT 1) AS sentiment "
            "FROM leads l WHERE l.sent_at IS NOT NULL"
        ).fetchall()

    def _bucket() -> dict:
        return {"sent": 0, "replied": 0, "positive": 0}

    groups = {
        "cv_cluster":    {},
        "body_length":   {},
        "subject_first": {},
        "weekday":       {},
    }

    for r in rows:
        replied = bool(r["replied_at"])
        positive = replied and (r["sentiment"] or "").lower() == "positive"

        keys = {
            "cv_cluster":    (r["cv_cluster"] or "(none)"),
            "body_length":   bucket_len(r["gen_body"]),
            "subject_first": subject_prefix(r["gen_subject"]),
            "weekday":       weekday(r["sent_at"]),
        }
        for group, key in keys.items():
            b = groups[group].setdefault(key, _bucket())
            b["sent"] += 1
            if replied:
                b["replied"] += 1
            if positive:
                b["positive"] += 1

    def pct(n: int, d: int) -> float:
        return round(n / d * 100, 1) if d else 0.0

    def serialise(by: dict) -> list[dict]:
        # Sort so UI can show best-performing first but small buckets
        # don't dominate. Require >=3 sent to rank by reply rate.
        items = []
        for k, v in by.items():
            items.append({
                "key": k,
                "sent": v["sent"],
                "replied": v["replied"],
                "positive": v["positive"],
                "reply_rate_pct": pct(v["replied"], v["sent"]),
                "positive_rate_pct": pct(v["positive"], v["sent"]),
            })
        items.sort(
            key=lambda x: (x["sent"] >= 3, x["reply_rate_pct"], x["sent"]),
            reverse=True,
        )
        return items

    total_sent = len(rows)
    total_replied = sum(1 for r in rows if r["replied_at"])
    total_positive = sum(
        1 for r in rows
        if r["replied_at"] and (r["sentiment"] or "").lower() == "positive"
    )

    return {
        "totals": {
            "sent": total_sent,
            "replied": total_replied,
            "positive": total_positive,
            "reply_rate_pct": pct(total_replied, total_sent),
            "positive_rate_pct": pct(total_positive, total_sent),
        },
        "by_cv_cluster":    serialise(groups["cv_cluster"]),
        "by_body_length":   serialise(groups["body_length"]),
        "by_subject_first": serialise(groups["subject_first"]),
        "by_weekday":       serialise(groups["weekday"]),
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
