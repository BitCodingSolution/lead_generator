"""
LinkedIn source — FastAPI router (Phase 1).

Only the read-only surface is live in Phase 1:
    GET /api/linkedin/overview
    GET /api/linkedin/leads
    GET /api/linkedin/safety

Write endpoints (ingest, send, Gmail OAuth, Claude drafts) ship in later
phases per Database/LinkedIn Data/PLAN.md and are stubbed here with 501s so
the frontend can wire buttons against stable URLs.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import secrets
import threading
import time
from typing import Optional

import requests
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from linkedin_db import connect, init
from linkedin_claude import generate_draft as _claude_generate
import linkedin_gmail as gmail

DAILY_CAP = 20
WARNING_PAUSE_DAYS = 7

# Phrases signalling LinkedIn flagged the account — extension forwards these.
WARNING_PHRASES_RE = re.compile(
    r"(restricted your (?:account|access)|temporarily (?:limited|restricted)|"
    r"unusual activity|automated activity|verify your identity|"
    r"confirm you'?re not a robot)",
    re.IGNORECASE,
)

# Jaydip-note phrases that mean "dead lead — move to Recyclebin". Matches the
# legacy Apps Script REJECTION_NOTE_RE.
REJECTION_NOTE_RE = re.compile(
    r"\b(rejected?|not interested|no interest|declined|not a fit|dead lead|no reply)\b",
    re.IGNORECASE,
)

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])

# Ensure schema exists on import (safe/idempotent).
init()


# ---------- models ----------


class LinkedInLead(BaseModel):
    id: int
    post_url: str
    posted_by: Optional[str]
    company: Optional[str]
    role: Optional[str]
    tech_stack: Optional[str]
    location: Optional[str]
    email: Optional[str]
    status: str
    gen_subject: Optional[str]
    cv_cluster: Optional[str]
    first_seen_at: str
    last_seen_at: str
    sent_at: Optional[str]
    replied_at: Optional[str]
    needs_attention: int


class OverviewResponse(BaseModel):
    total: int
    new: int
    drafted: int
    queued: int
    sent_today: int
    replied: int
    bounced: int
    quota_used: int
    quota_cap: int
    gmail_connected: bool
    autopilot_enabled: bool
    safety_mode: str
    warning_paused_until: Optional[str]


class SafetyState(BaseModel):
    daily_sent_count: int
    daily_sent_date: Optional[str]
    last_send_at: Optional[str]
    consecutive_failures: int
    warning_paused_until: Optional[str]
    autopilot_enabled: bool
    autopilot_hour: int
    safety_mode: str


# ---------- helpers ----------


def _today() -> str:
    return dt.date.today().isoformat()


def _roll_daily_counter(con) -> None:
    row = con.execute(
        "SELECT daily_sent_count, daily_sent_date FROM safety_state WHERE id=1"
    ).fetchone()
    if row and row["daily_sent_date"] != _today():
        con.execute(
            "UPDATE safety_state SET daily_sent_count=0, daily_sent_date=? WHERE id=1",
            (_today(),),
        )
        con.commit()


# ---------- read endpoints ----------


@router.get("/overview", response_model=OverviewResponse)
def overview() -> OverviewResponse:
    with connect() as con:
        _roll_daily_counter(con)

        def cnt(where: str, params: tuple = ()) -> int:
            return con.execute(
                f"SELECT COUNT(*) FROM leads WHERE {where}", params
            ).fetchone()[0]

        total = con.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        new = cnt("status = 'New'")
        drafted = cnt("status = 'Drafted'")
        queued = cnt("status IN ('Queued', 'Sending')")
        sent_today = cnt("DATE(sent_at) = ?", (_today(),))
        replied = cnt("status = 'Replied'")
        bounced = cnt("status = 'Bounced'")

        safety = con.execute("SELECT * FROM safety_state WHERE id=1").fetchone()
        gmail = con.execute("SELECT email FROM gmail_auth WHERE id=1").fetchone()

        return OverviewResponse(
            total=total,
            new=new,
            drafted=drafted,
            queued=queued,
            sent_today=sent_today,
            replied=replied,
            bounced=bounced,
            quota_used=safety["daily_sent_count"] if safety else 0,
            quota_cap=DAILY_CAP,
            gmail_connected=bool(gmail and gmail["email"]),
            autopilot_enabled=bool(safety["autopilot_enabled"]) if safety else False,
            safety_mode=safety["safety_mode"] if safety else "max",
            warning_paused_until=safety["warning_paused_until"] if safety else None,
        )


@router.get("/leads")
def list_leads(
    status: Optional[str] = Query(None),
    needs_attention: Optional[bool] = Query(None),
    q: Optional[str] = Query(None),
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
    if q:
        clauses.append(
            "(company LIKE ? OR posted_by LIKE ? OR role LIKE ? OR email LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with connect() as con:
        total = con.execute(
            f"SELECT COUNT(*) FROM leads {where}", tuple(params)
        ).fetchone()[0]
        rows = con.execute(
            f"SELECT id, post_url, posted_by, company, role, tech_stack, location, "
            f"email, status, gen_subject, cv_cluster, first_seen_at, last_seen_at, "
            f"sent_at, replied_at, needs_attention "
            f"FROM leads {where} ORDER BY last_seen_at DESC LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        ).fetchall()
        return {"rows": [dict(r) for r in rows], "total": total}


class SafetyPatch(BaseModel):
    safety_mode: Optional[str] = None     # max | normal
    autopilot_enabled: Optional[bool] = None
    autopilot_hour: Optional[int] = Field(default=None, ge=0, le=23)
    clear_warning_pause: Optional[bool] = None


@router.post("/safety")
def update_safety(patch: SafetyPatch):
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True}
    mode = updates.get("safety_mode")
    if mode is not None and mode not in ("max", "normal"):
        raise HTTPException(400, "safety_mode must be 'max' or 'normal'")

    db_updates: dict = {}
    if "safety_mode" in updates:
        db_updates["safety_mode"] = updates["safety_mode"]
    if "autopilot_enabled" in updates:
        db_updates["autopilot_enabled"] = 1 if updates["autopilot_enabled"] else 0
    if "autopilot_hour" in updates:
        db_updates["autopilot_hour"] = int(updates["autopilot_hour"])
    if updates.get("clear_warning_pause"):
        db_updates["warning_paused_until"] = None

    sets = ", ".join(f"{k} = ?" for k in db_updates)
    with connect() as con:
        con.execute(
            f"UPDATE safety_state SET {sets} WHERE id = 1",
            list(db_updates.values()),
        )
        _log_event(con, "safety_update", meta=updates)
        con.commit()
    return {"ok": True, "updated": list(db_updates.keys())}


@router.get("/safety", response_model=SafetyState)
def get_safety() -> SafetyState:
    with connect() as con:
        _roll_daily_counter(con)
        r = con.execute("SELECT * FROM safety_state WHERE id=1").fetchone()
        if r is None:
            raise HTTPException(500, "safety_state missing")
        return SafetyState(
            daily_sent_count=r["daily_sent_count"],
            daily_sent_date=r["daily_sent_date"],
            last_send_at=r["last_send_at"],
            consecutive_failures=r["consecutive_failures"],
            warning_paused_until=r["warning_paused_until"],
            autopilot_enabled=bool(r["autopilot_enabled"]),
            autopilot_hour=r["autopilot_hour"],
            safety_mode=r["safety_mode"],
        )


# ---------- extension auth ----------


def _require_ext_key(x_ext_key: Optional[str]) -> str:
    """Validate extension API key. Raises 401 on miss, returns the key on hit.
    Side effect: updates last_used_at."""
    if not x_ext_key:
        raise HTTPException(401, "Missing X-Ext-Key header")
    with connect() as con:
        row = con.execute(
            "SELECT key FROM extension_keys WHERE key = ?", (x_ext_key,)
        ).fetchone()
        if row is None:
            raise HTTPException(401, "Invalid extension key")
        con.execute(
            "UPDATE extension_keys SET last_used_at = ? WHERE key = ?",
            (dt.datetime.now().isoformat(timespec="seconds"), x_ext_key),
        )
        con.commit()
    return x_ext_key


class ExtensionKeyIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)


@router.get("/extension/keys")
def list_extension_keys():
    with connect() as con:
        rows = con.execute(
            "SELECT key, label, created_at, last_used_at "
            "FROM extension_keys ORDER BY created_at DESC"
        ).fetchall()
        return {"rows": [dict(r) for r in rows]}


@router.post("/extension/keys")
def create_extension_key(payload: ExtensionKeyIn):
    key = f"li_{secrets.token_urlsafe(24)}"
    now = dt.datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        con.execute(
            "INSERT INTO extension_keys (key, label, created_at) VALUES (?, ?, ?)",
            (key, payload.label.strip(), now),
        )
        con.commit()
    return {"key": key, "label": payload.label.strip(), "created_at": now}


@router.post("/extension/keys/{key}/revoke")
def revoke_extension_key(key: str):
    with connect() as con:
        cur = con.execute("DELETE FROM extension_keys WHERE key = ?", (key,))
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Key not found")
    return {"revoked": key}


# ---------- ingest (extension → dashboard) ----------


class IngestPost(BaseModel):
    post_url: str = Field(min_length=1)
    posted_by: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    tech_stack: Optional[str] = None
    rate: Optional[str] = None
    location: Optional[str] = None
    tags: Optional[str] = None
    post_text: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class IngestBatch(BaseModel):
    leads: list[IngestPost]


def _log_event(con, kind: str, lead_id: Optional[int] = None, meta: Optional[dict] = None):
    con.execute(
        "INSERT INTO events (at, kind, lead_id, meta_json) VALUES (?, ?, ?, ?)",
        (
            dt.datetime.now().isoformat(timespec="seconds"),
            kind,
            lead_id,
            json.dumps(meta) if meta else None,
        ),
    )


def _upsert_lead(con, p: IngestPost) -> tuple[int, str]:
    """Insert or update by post_url. Returns (lead_id, action)."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    row = con.execute(
        "SELECT id, email, status FROM leads WHERE post_url = ?", (p.post_url,)
    ).fetchone()

    if row is None:
        cur = con.execute(
            """INSERT INTO leads (post_url, posted_by, company, role, tech_stack,
               rate, location, tags, post_text, email, phone,
               first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                p.post_url, p.posted_by, p.company, p.role, p.tech_stack,
                p.rate, p.location, p.tags, p.post_text, p.email, p.phone,
                now, now,
            ),
        )
        return cur.lastrowid, "inserted"

    lead_id = row["id"]
    # Refresh last_seen + fill in any now-known fields without clobbering
    # manually-edited ones. Email is only overwritten if we previously had
    # nothing.
    fields = {
        "last_seen_at": now,
        "posted_by": p.posted_by,
        "company": p.company,
        "role": p.role,
        "tech_stack": p.tech_stack,
        "rate": p.rate,
        "location": p.location,
        "tags": p.tags,
        "post_text": p.post_text,
        "phone": p.phone,
    }
    if not row["email"] and p.email:
        fields["email"] = p.email

    sets = ", ".join(f"{k} = COALESCE(?, {k})" for k in fields)
    vals = [*fields.values(), lead_id]
    con.execute(f"UPDATE leads SET {sets} WHERE id = ?", vals)
    return lead_id, "updated"


@router.post("/ingest")
def ingest(
    payload: IngestBatch,
    x_ext_key: Optional[str] = Header(default=None, alias="X-Ext-Key"),
):
    _require_ext_key(x_ext_key)
    inserted = 0
    updated = 0
    with connect() as con:
        for p in payload.leads:
            _, action = _upsert_lead(con, p)
            if action == "inserted":
                inserted += 1
            else:
                updated += 1
        _log_event(con, "ingest", meta={"inserted": inserted, "updated": updated})
        con.commit()
    return {"inserted": inserted, "updated": updated, "total": len(payload.leads)}


# ---------- account-warning pause ----------


class AccountWarning(BaseModel):
    phrase: str
    url: Optional[str] = None


@router.post("/account-warning")
def account_warning(
    payload: AccountWarning,
    x_ext_key: Optional[str] = Header(default=None, alias="X-Ext-Key"),
):
    _require_ext_key(x_ext_key)
    if not WARNING_PHRASES_RE.search(payload.phrase or ""):
        raise HTTPException(400, "Phrase does not match any known warning signature")
    paused_until = (
        dt.datetime.now() + dt.timedelta(days=WARNING_PAUSE_DAYS)
    ).isoformat(timespec="seconds")
    with connect() as con:
        con.execute(
            "UPDATE safety_state SET warning_paused_until = ? WHERE id = 1",
            (paused_until,),
        )
        _log_event(con, "warning", meta={"phrase": payload.phrase, "url": payload.url})
        con.commit()
    return {"paused_until": paused_until}


# ---------- lead detail, edit, archive, restore ----------


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int):
    with connect() as con:
        r = con.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "Lead not found")
        return dict(r)


class LeadPatch(BaseModel):
    gen_subject: Optional[str] = None
    gen_body: Optional[str] = None
    jaydip_note: Optional[str] = None
    email_mode: Optional[str] = None
    needs_attention: Optional[bool] = None


@router.post("/leads/{lead_id}")
def patch_lead(lead_id: int, patch: LeadPatch):
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True, "updated": 0}
    if "needs_attention" in updates:
        updates["needs_attention"] = 1 if updates["needs_attention"] else 0

    auto_archived = False
    with connect() as con:
        row = con.execute("SELECT id FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Lead not found")
        sets = ", ".join(f"{k} = ?" for k in updates)
        con.execute(f"UPDATE leads SET {sets} WHERE id = ?", [*updates.values(), lead_id])
        # If a draft was edited, ensure status reflects Drafted at minimum.
        if "gen_subject" in updates or "gen_body" in updates:
            con.execute(
                "UPDATE leads SET status = 'Drafted' "
                "WHERE id = ? AND status IN ('New', 'Skipped')",
                (lead_id,),
            )
        # Rejection-note auto-move (matches legacy Apps Script behaviour).
        note = updates.get("jaydip_note")
        if note and REJECTION_NOTE_RE.search(note):
            _archive_lead(con, lead_id, reason="user_note")
            auto_archived = True
        con.commit()

    return {"ok": True, "updated": len(updates), "auto_archived": auto_archived}


class ArchiveRequest(BaseModel):
    reason: str = Field(default="manual", max_length=40)


def _archive_lead(con, lead_id: int, reason: str) -> None:
    row = con.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Lead not found")
    payload = {k: row[k] for k in row.keys()}
    con.execute(
        "INSERT OR REPLACE INTO recyclebin "
        "(original_id, post_url, payload_json, reason, moved_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            lead_id,
            row["post_url"],
            json.dumps(payload),
            reason,
            dt.datetime.now().isoformat(timespec="seconds"),
        ),
    )
    con.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    _log_event(con, "archive", lead_id=lead_id, meta={"reason": reason})


@router.post("/leads/{lead_id}/archive")
def archive_lead(lead_id: int, payload: ArchiveRequest):
    with connect() as con:
        _archive_lead(con, lead_id, payload.reason)
        con.commit()
    return {"archived": lead_id, "reason": payload.reason}


@router.post("/leads/{lead_id}/restore")
def restore_lead(lead_id: int):
    """Restore from recyclebin. `lead_id` here is the recyclebin row id."""
    with connect() as con:
        r = con.execute(
            "SELECT * FROM recyclebin WHERE id = ?", (lead_id,)
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
            f"INSERT INTO leads ({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )
        new_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("DELETE FROM recyclebin WHERE id = ?", (lead_id,))
        _log_event(con, "restore", lead_id=new_id)
        con.commit()
    return {"restored": new_id}


@router.get("/recyclebin")
def list_recyclebin(limit: int = 200):
    with connect() as con:
        rows = con.execute(
            "SELECT id, original_id, post_url, reason, moved_at, payload_json "
            "FROM recyclebin ORDER BY moved_at DESC LIMIT ?",
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


# ---------- Claude draft generation ----------


@router.post("/drafts/{lead_id}/generate")
def generate_draft(lead_id: int):
    with connect() as con:
        row = con.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Lead not found")

    try:
        result = _claude_generate(
            posted_by=row["posted_by"] or "",
            company=row["company"] or "",
            role=row["role"] or "",
            tech_stack=row["tech_stack"] or "",
            location=row["location"] or "",
            post_text=row["post_text"] or "",
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(502, f"Claude Bridge unreachable: {e}")
    except Exception as e:
        raise HTTPException(500, f"Draft generation failed: {e}")

    with connect() as con:
        # Auto-archive on Claude skip decision.
        if result.should_skip:
            con.execute(
                "UPDATE leads SET gen_subject = ?, gen_body = ?, email_mode = ?, "
                "cv_cluster = ?, skip_reason = ?, skip_source = ?, "
                "status = 'Skipped' WHERE id = ?",
                (
                    result.subject, result.body, result.email_mode,
                    result.cv_cluster, result.skip_reason, result.skip_source,
                    lead_id,
                ),
            )
            _log_event(con, "draft_skipped", lead_id=lead_id,
                       meta={"reason": result.skip_reason})
            _archive_lead(con, lead_id, reason=f"auto_skip:{result.skip_reason}")
            con.commit()
            return {
                "status": "skipped",
                "skip_reason": result.skip_reason,
                "archived": True,
            }

        con.execute(
            "UPDATE leads SET gen_subject = ?, gen_body = ?, email_mode = ?, "
            "cv_cluster = ?, status = 'Drafted', skip_reason = NULL, "
            "skip_source = NULL WHERE id = ?",
            (
                result.subject, result.body, result.email_mode,
                result.cv_cluster, lead_id,
            ),
        )
        _log_event(con, "draft", lead_id=lead_id,
                   meta={"mode": result.email_mode, "cv": result.cv_cluster})
        con.commit()

    return {
        "status": "drafted",
        "subject": result.subject,
        "body": result.body,
        "email_mode": result.email_mode,
        "cv_cluster": result.cv_cluster,
    }


# ---------- Gmail connect / test / disconnect ----------


class GmailConnectIn(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    app_password: str = Field(min_length=10, max_length=32)


@router.get("/gmail/status")
def gmail_status():
    with connect() as con:
        r = con.execute(
            "SELECT email, connected_at, last_verified_at "
            "FROM gmail_auth WHERE id=1"
        ).fetchone()
        if r is None or not r["email"]:
            return {"connected": False, "email": None, "connected_at": None}
        return {
            "connected": True,
            "email": r["email"],
            "connected_at": r["connected_at"],
            "last_verified_at": r["last_verified_at"],
        }


@router.post("/gmail/connect")
def gmail_connect(payload: GmailConnectIn):
    try:
        check = gmail.verify_credentials(
            payload.email.strip(), payload.app_password.strip()
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    gmail.save_credentials(payload.email.strip(), payload.app_password.strip())
    with connect() as con:
        _log_event(con, "gmail_connect", meta={"email": payload.email, **check})
        con.commit()
    return {"connected": True, "email": payload.email, **check}


@router.post("/gmail/test")
def gmail_test():
    creds = gmail.get_credentials()
    if not creds:
        raise HTTPException(400, "Gmail not connected")
    try:
        check = gmail.verify_credentials(*creds)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    now = dt.datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        con.execute(
            "UPDATE gmail_auth SET last_verified_at = ? WHERE id = 1", (now,),
        )
        con.commit()
    return {"ok": True, **check, "last_verified_at": now}


@router.post("/gmail/disconnect")
def gmail_disconnect():
    gmail.clear_credentials()
    with connect() as con:
        _log_event(con, "gmail_disconnect")
        con.commit()
    return {"connected": False}


# ---------- safety gate ----------


def _check_safety_before_send(con, *, allow_quiet_hours: bool = False) -> None:
    """Raise HTTPException if any rail blocks sending. Returns cleanly otherwise."""
    _roll_daily_counter(con)
    s = con.execute("SELECT * FROM safety_state WHERE id=1").fetchone()
    if s is None:
        raise HTTPException(500, "safety_state missing")

    if s["warning_paused_until"]:
        try:
            paused = dt.datetime.fromisoformat(s["warning_paused_until"])
            if paused > dt.datetime.now():
                raise HTTPException(
                    423,
                    f"Account-warning pause active until {s['warning_paused_until']}",
                )
        except ValueError:
            pass

    if s["daily_sent_count"] >= DAILY_CAP:
        raise HTTPException(
            429, f"Daily cap of {DAILY_CAP} already reached"
        )

    if not allow_quiet_hours:
        h = dt.datetime.now().hour
        if h >= 23 or h < 7:
            raise HTTPException(
                423, "Quiet hours active (23:00–07:00 local)"
            )


def _record_send(con, lead_id: int, message_id: str, sent_at: str) -> None:
    con.execute(
        "UPDATE leads SET status = 'Sent', sent_at = ?, sent_message_id = ? "
        "WHERE id = ?",
        (sent_at, message_id, lead_id),
    )
    con.execute(
        "UPDATE safety_state SET daily_sent_count = daily_sent_count + 1, "
        "last_send_at = ?, consecutive_failures = 0 WHERE id = 1",
        (sent_at,),
    )
    _log_event(con, "send", lead_id=lead_id, meta={"msg_id": message_id})


def _record_failure(con, lead_id: int, err: str) -> None:
    con.execute(
        "UPDATE safety_state SET consecutive_failures = consecutive_failures + 1 "
        "WHERE id = 1"
    )
    _log_event(con, "send_error", lead_id=lead_id, meta={"error": err[:400]})


# ---------- send one ----------


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
        _check_safety_before_send(con)

    try:
        result = gmail.send_email(
            to=lead["email"],
            subject=lead["gen_subject"],
            body=lead["gen_body"],
        )
    except Exception as e:
        with connect() as con:
            _record_failure(con, lead_id, str(e))
            con.commit()
        raise HTTPException(502, f"Send failed: {e}")

    with connect() as con:
        _record_send(con, lead_id, result.message_id, result.sent_at)
        con.commit()
    return {"sent_at": result.sent_at, "message_id": result.message_id}


# ---------- write endpoints (Phase 5+) — intentional 501s ----------


def _not_yet(phase: str):
    raise HTTPException(
        status_code=501,
        detail=f"LinkedIn: not implemented yet — lands in {phase}",
    )


# ---------- batch send + autopilot ----------

BATCH_JITTER_MIN_S = 60
BATCH_JITTER_MAX_S = 90

_batch_lock = threading.Lock()
_batch_state: dict = {
    "running": False,
    "total": 0,
    "sent": 0,
    "failed": 0,
    "skipped": 0,
    "started_at": None,
    "finished_at": None,
    "current_lead_id": None,
    "current_email": None,
    "last_error": None,
    "source": None,        # "manual" | "autopilot"
    "stop_requested": False,
}


class BatchSendIn(BaseModel):
    count: int = Field(default=5, ge=1, le=DAILY_CAP)
    source: str = Field(default="manual")   # manual | autopilot


def _pick_ready_leads(con, limit: int) -> list[int]:
    """Leads that are fully ready to send: Drafted, have email, no private
    note. Ordered oldest-first so a batch drains the queue sensibly."""
    rows = con.execute(
        "SELECT id FROM leads "
        "WHERE status = 'Drafted' "
        "  AND email IS NOT NULL AND TRIM(email) != '' "
        "  AND gen_subject IS NOT NULL AND TRIM(gen_subject) != '' "
        "  AND gen_body    IS NOT NULL AND TRIM(gen_body)    != '' "
        "  AND (jaydip_note IS NULL OR TRIM(jaydip_note) = '') "
        "ORDER BY first_seen_at ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["id"] for r in rows]


def _batch_worker(lead_ids: list[int], source: str) -> None:
    try:
        for idx, lead_id in enumerate(lead_ids):
            if _batch_state["stop_requested"]:
                break

            with connect() as con:
                try:
                    _check_safety_before_send(con)
                except HTTPException as e:
                    _batch_state["last_error"] = str(e.detail)
                    break

                lead = con.execute(
                    "SELECT email, gen_subject, gen_body, jaydip_note, status "
                    "FROM leads WHERE id = ?", (lead_id,),
                ).fetchone()
                if lead is None or lead["status"] == "Sent" or (
                    lead["jaydip_note"] or ""
                ).strip():
                    _batch_state["skipped"] += 1
                    continue

            _batch_state["current_lead_id"] = lead_id
            _batch_state["current_email"] = lead["email"]

            try:
                result = gmail.send_email(
                    to=lead["email"],
                    subject=lead["gen_subject"],
                    body=lead["gen_body"],
                )
                with connect() as con:
                    _record_send(con, lead_id, result.message_id, result.sent_at)
                    con.commit()
                _batch_state["sent"] += 1
            except Exception as e:
                with connect() as con:
                    _record_failure(con, lead_id, str(e))
                    con.commit()
                _batch_state["failed"] += 1
                _batch_state["last_error"] = str(e)[:200]

            # Jitter between sends, but not after the final one.
            if idx < len(lead_ids) - 1 and not _batch_state["stop_requested"]:
                wait = random.randint(BATCH_JITTER_MIN_S, BATCH_JITTER_MAX_S)
                for _ in range(wait):
                    if _batch_state["stop_requested"]:
                        break
                    time.sleep(1)
    finally:
        _batch_state["running"] = False
        _batch_state["current_lead_id"] = None
        _batch_state["current_email"] = None
        _batch_state["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _batch_state["stop_requested"] = False
        _batch_state["source"] = source
        with connect() as con:
            _log_event(con, "batch_end", meta={
                "source": source,
                "sent": _batch_state["sent"],
                "failed": _batch_state["failed"],
                "skipped": _batch_state["skipped"],
            })
            con.commit()


@router.post("/send/batch")
def send_batch(payload: BatchSendIn):
    with _batch_lock:
        if _batch_state["running"]:
            raise HTTPException(409, "A batch is already running")

        with connect() as con:
            _check_safety_before_send(con)
            remaining_quota = max(
                0,
                DAILY_CAP - con.execute(
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
        t = threading.Thread(
            target=_batch_worker,
            args=(lead_ids, payload.source),
            daemon=True,
        )
        t.start()

    return {"started": True, "total": len(lead_ids), "source": payload.source}


@router.get("/send/batch/status")
def batch_status():
    return dict(_batch_state)


@router.post("/send/batch/stop")
def batch_stop():
    if not _batch_state["running"]:
        return {"stopped": False, "message": "Not running"}
    _batch_state["stop_requested"] = True
    return {"stopped": True}


# ---------- autopilot tick (called by main.py scheduler) ----------

_autopilot_state = {"last_fired_date": None}


def _autopilot_tick() -> None:
    """Checks safety_state.autopilot_*. If enabled and at-or-past the target
    hour, fires one batch for the day. Safe to call every minute."""
    with connect() as con:
        s = con.execute(
            "SELECT autopilot_enabled, autopilot_hour FROM safety_state WHERE id=1"
        ).fetchone()
    if not s or not s["autopilot_enabled"]:
        return

    now = dt.datetime.now()
    today = now.date().isoformat()
    if _autopilot_state["last_fired_date"] == today:
        return
    if now.hour < int(s["autopilot_hour"]):
        return
    if _batch_state["running"]:
        return
    if gmail.get_credentials() is None:
        return

    try:
        send_batch(BatchSendIn(count=DAILY_CAP, source="autopilot"))
        _autopilot_state["last_fired_date"] = today
    except HTTPException as e:
        # Dampen log spam — only record truly new error conditions.
        with connect() as con:
            _log_event(con, "autopilot_skip",
                       meta={"status": e.status_code, "detail": str(e.detail)[:200]})
            con.commit()
        _autopilot_state["last_fired_date"] = today


@router.get("/replies")
def list_replies(limit: int = 100):
    with connect() as con:
        rows = con.execute(
            "SELECT r.id, r.lead_id, r.from_email, r.subject, r.snippet, "
            "r.received_at, r.kind, l.company, l.posted_by, l.email AS lead_email "
            "FROM replies r LEFT JOIN leads l ON l.id = r.lead_id "
            "ORDER BY r.received_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {"rows": [dict(r) for r in rows]}


def _match_reply_to_lead(con, in_reply_to: str, references: str) -> Optional[int]:
    """Find the lead whose sent_message_id appears in the reply's
    In-Reply-To or References header."""
    candidates: list[str] = []
    if in_reply_to:
        candidates.append(in_reply_to.strip("<>").strip())
    for ref in re.split(r"\s+", references or ""):
        ref = ref.strip().strip("<>")
        if ref:
            candidates.append(ref)
    if not candidates:
        return None
    placeholders = ",".join(["?"] * len(candidates))
    row = con.execute(
        f"SELECT id FROM leads WHERE sent_message_id IN ({placeholders}) LIMIT 1",
        candidates,
    ).fetchone()
    return row["id"] if row else None


def _poll_and_store() -> dict:
    """Fetch new inbox messages, match against sent leads, update lead status
    on replies/bounces. Returns counts."""
    with connect() as con:
        r = con.execute(
            "SELECT imap_uid_seen FROM gmail_auth WHERE id = 1"
        ).fetchone()
        since_uid = int(r["imap_uid_seen"]) if r and r["imap_uid_seen"] else 0

    msgs, new_uid = gmail.poll_recent(since_uid=since_uid)

    counts = {"fetched": len(msgs), "replies": 0, "bounces": 0, "auto_replies": 0, "matched": 0}

    with connect() as con:
        for m in msgs:
            lead_id = _match_reply_to_lead(con, m.in_reply_to, m.references)
            if not lead_id:
                continue
            counts["matched"] += 1
            con.execute(
                "INSERT OR IGNORE INTO replies "
                "(lead_id, gmail_msg_id, from_email, subject, snippet, "
                "received_at, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (lead_id, m.message_id, m.from_email, m.subject, m.snippet,
                 m.received_at, m.kind),
            )
            if m.kind == "reply":
                counts["replies"] += 1
                con.execute(
                    "UPDATE leads SET status = 'Replied', replied_at = ?, "
                    "needs_attention = 1 WHERE id = ? AND status != 'Replied'",
                    (m.received_at, lead_id),
                )
            elif m.kind == "bounce":
                counts["bounces"] += 1
                con.execute(
                    "UPDATE leads SET status = 'Bounced', bounced_at = ? "
                    "WHERE id = ?",
                    (m.received_at, lead_id),
                )
            else:
                counts["auto_replies"] += 1

            _log_event(con, f"inbox_{m.kind}", lead_id=lead_id,
                       meta={"msg_id": m.message_id, "from": m.from_email})

        if new_uid > since_uid:
            con.execute(
                "UPDATE gmail_auth SET imap_uid_seen = ? WHERE id = 1",
                (new_uid,),
            )
        con.commit()

    return {**counts, "since_uid": since_uid, "new_uid": new_uid}


@router.post("/replies/poll")
def poll_replies():
    try:
        return _poll_and_store()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Poll failed: {e}")
