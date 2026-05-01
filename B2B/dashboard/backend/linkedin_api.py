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
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from linkedin_db import connect, init
from linkedin_claude import (
    BridgeParseError,
    BridgeUnreachable,
    bridge_is_up,
    draft_variety_key,
    generate_draft as _claude_generate,
)
import linkedin_claude
import linkedin_scoring
import linkedin_gmail as gmail
import linkedin_extras as extras

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
    phone: Optional[str]
    status: str
    call_status: Optional[str] = None
    reviewed_at: Optional[str] = None
    jaydip_note: Optional[str] = None
    open_count: int = 0
    first_opened_at: Optional[str] = None
    last_opened_at: Optional[str] = None
    scheduled_send_at: Optional[str] = None
    ooo_nudge_at: Optional[str] = None
    ooo_nudge_sent_at: Optional[str] = None
    fit_score: Optional[int] = None
    fit_score_reasons: Optional[str] = None
    gen_subject: Optional[str]
    cv_cluster: Optional[str]
    first_seen_at: str
    last_seen_at: str
    sent_at: Optional[str]
    replied_at: Optional[str]
    needs_attention: int


class AutoPausedAccount(BaseModel):
    id: int
    email: str
    reason: str


class OverviewResponse(BaseModel):
    total: int
    new: int
    drafted: int
    queued: int
    sent_today: int
    replied: int
    # Replies still awaiting Jaydip's action (handled = 0). Drives the
    # "X pending" sub-line on the Replied KPI so a glance at the dashboard
    # tells him whether any conversations need triage.
    replied_pending: int = 0
    bounced: int
    quota_used: int
    quota_cap: int
    gmail_connected: bool
    autopilot_enabled: bool
    safety_mode: str
    warning_paused_until: Optional[str]
    auto_paused_accounts: list[AutoPausedAccount] = []


class AutopilotTodayRun(BaseModel):
    fired_at: str
    total_queued: int
    status: str


class SafetyState(BaseModel):
    daily_sent_count: int
    daily_sent_date: Optional[str]
    last_send_at: Optional[str]
    consecutive_failures: int
    warning_paused_until: Optional[str]
    autopilot_enabled: bool
    autopilot_hour: int
    autopilot_minute: int
    # None = send the full effective daily cap; int = cap at this many.
    autopilot_count: Optional[int]
    autopilot_tz: str
    business_hours_only: bool
    safety_mode: str
    # Auto follow-up sequencer: when on, _followups_tick fires
    # run_followups() once a day at followups_hour local. Falls back to
    # the cadence in linkedin_extras.FOLLOWUP_DAYS (default 3, 7).
    followups_autopilot: bool = False
    followups_hour: int = 11
    # Populated when autopilot has already fired (or been skipped) today.
    # UI uses this to show a "Already ran at HH:MM" state + expose a manual
    # reset button so the user can re-fire for the same day.
    autopilot_today: Optional[AutopilotTodayRun] = None


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
        # Pending = unhandled inbound replies. Mirror exactly what the
        # /replies UI shows in "Unhandled only" mode so the KPI counter
        # never disagrees with the inbox feed:
        #   - kind = 'reply' (excludes bounces / auto-replies)
        #   - handled_at IS NULL (still needs action)
        # Count rows (NOT distinct lead_id) — the inbox renders one row
        # per reply, so two unhandled mails from the same lead must show
        # as 2 in the badge.
        replied_pending = con.execute(
            "SELECT COUNT(*) FROM replies "
            "WHERE kind = 'reply' AND handled_at IS NULL"
        ).fetchone()[0]
        bounced = cnt("status = 'Bounced'")

        safety = con.execute("SELECT * FROM safety_state WHERE id=1").fetchone()
        gmail_row = con.execute(
            "SELECT 1 FROM gmail_accounts LIMIT 1"
        ).fetchone()
        auto_paused = con.execute(
            "SELECT id, email, paused_reason FROM gmail_accounts "
            "WHERE status = 'paused' AND paused_reason IS NOT NULL"
        ).fetchall()

        return OverviewResponse(
            total=total,
            new=new,
            drafted=drafted,
            queued=queued,
            sent_today=sent_today,
            replied=replied,
            replied_pending=replied_pending,
            bounced=bounced,
            quota_used=safety["daily_sent_count"] if safety else 0,
            quota_cap=_effective_daily_cap(con),
            gmail_connected=gmail_row is not None,
            autopilot_enabled=bool(safety["autopilot_enabled"]) if safety else False,
            safety_mode=safety["safety_mode"] if safety else "max",
            warning_paused_until=safety["warning_paused_until"] if safety else None,
            auto_paused_accounts=[
                AutoPausedAccount(id=r["id"], email=r["email"],
                                  reason=r["paused_reason"])
                for r in auto_paused
            ],
        )


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
            "UPDATE leads SET needs_attention = 1, remind_at = NULL "
            "WHERE remind_at IS NOT NULL AND remind_at <= ?",
            (now_iso,),
        )
        con.commit()

        total = con.execute(
            f"SELECT COUNT(*) FROM leads {where}", tuple(params)
        ).fetchone()[0]
        rows = con.execute(
            f"SELECT id, post_url, posted_by, company, role, tech_stack, location, "
            f"email, phone, status, gen_subject, cv_cluster, first_seen_at, last_seen_at, "
            f"sent_at, replied_at, needs_attention, call_status, reviewed_at, "
            f"jaydip_note, open_count, first_opened_at, last_opened_at, "
            f"scheduled_send_at, ooo_nudge_at, ooo_nudge_sent_at, "
            f"fit_score, fit_score_reasons, remind_at "
            f"FROM leads {where} {order_sql} LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        ).fetchall()
        # Compute which CV clusters are currently uploaded so the UI can
        # flag leads whose matched specialty slot is empty BEFORE the user
        # clicks Send (which would 400 on cv_required_but_missing). One
        # roundtrip covers the whole page.
        present_clusters = {
            r[0] for r in con.execute("SELECT cluster FROM cvs").fetchall()
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
                "FROM leads "
                "WHERE posted_by IS NOT NULL AND TRIM(posted_by) != '' "
                "  AND DATE(first_seen_at) >= ? "
                "GROUP BY posted_by HAVING n_companies >= 3",
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


def _lead_temperature(lead: dict) -> int:
    """A 0-100 'heat' score per lead. Combines:
       +30 if a positive reply landed
       +15 base for any reply
       +15 if email was opened in the last 7 days
       +10 if there's any open at all
       +10 for an explicit yellow/green call_status signal
       -20 if reviewed_at is set (we already triaged, less urgent)
       -10 for every 14d since last_seen_at (decay for cold leads)
       Capped to [0, 100]. Drives the inbox sort order so hot leads
       float to the top without the user needing to filter manually."""
    now = dt.datetime.now()
    score = 0
    # Reply signal — strongest positive.
    if lead.get("replied_at"):
        score += 15
        sent_pos = (lead.get("sentiment") or "").lower() == "positive" \
            or lead.get("call_status") == "green"
        if sent_pos:
            score += 30
    # Open signal — recipient at least loaded the pixel.
    open_count = int(lead.get("open_count") or 0)
    if open_count > 0:
        score += 10
        last_opened = lead.get("last_opened_at")
        if last_opened:
            try:
                age = (now - dt.datetime.fromisoformat(last_opened)).days
                if age <= 7:
                    score += 15
            except ValueError:
                pass
    # Manual triage signals from Jaydip.
    cs = (lead.get("call_status") or "").lower()
    if cs == "green":
        score += 20
    elif cs == "yellow":
        score += 10
    elif cs == "red":
        score -= 25
    # Bounced/Skipped → cold.
    if lead.get("status") in ("Bounced", "Skipped"):
        score -= 50
    # Already-handled penalty: if Jaydip has reviewed_at stamped, the
    # lead is less likely to need attention now.
    if lead.get("reviewed_at"):
        score -= 20
    # Recency decay — every 14 days since last_seen_at sheds 10pts so
    # truly cold rows rank below newer ones with similar signal.
    last_seen = lead.get("last_seen_at")
    if last_seen:
        try:
            age = (now - dt.datetime.fromisoformat(last_seen)).days
            score -= (age // 14) * 10
        except ValueError:
            pass
    return max(0, min(100, score))


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
                f"SELECT {', '.join(cols)} FROM leads {where} "
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
        ids = [r["id"] for r in con.execute("SELECT id FROM leads").fetchall()]
        for lead_id in ids:
            _rescore(con, lead_id)
        con.commit()
    return {"ok": True, "rescored": len(ids)}


class SafetyPatch(BaseModel):
    safety_mode: Optional[str] = None     # max | normal
    autopilot_enabled: Optional[bool] = None
    autopilot_hour: Optional[int] = Field(default=None, ge=0, le=23)
    autopilot_minute: Optional[int] = Field(default=None, ge=0, le=59)
    # 0 or null from the wire means "full cap"; otherwise cap at N.
    # Using -1 as the explicit "revert to full" sentinel so the client can
    # toggle between "limited" and "full" without ambiguity.
    autopilot_count: Optional[int] = Field(default=None, ge=-1, le=500)
    autopilot_tz: Optional[str] = Field(default=None, max_length=64)
    business_hours_only: Optional[bool] = None
    clear_warning_pause: Optional[bool] = None
    followups_autopilot: Optional[bool] = None
    followups_hour: Optional[int] = Field(default=None, ge=0, le=23)


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
    if "autopilot_minute" in updates:
        db_updates["autopilot_minute"] = int(updates["autopilot_minute"])
    if "autopilot_count" in updates:
        raw = updates["autopilot_count"]
        # -1 from the client is the "revert to full cap" sentinel.
        if raw is None or int(raw) <= 0:
            db_updates["autopilot_count"] = None
        else:
            db_updates["autopilot_count"] = int(raw)
    if "autopilot_tz" in updates:
        tz = (updates["autopilot_tz"] or "").strip()
        if tz:
            # Validate before persisting — zoneinfo raises on bad names.
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz)
            except Exception:
                raise HTTPException(400, f"Invalid IANA timezone: {tz}")
        db_updates["autopilot_tz"] = tz
    if "business_hours_only" in updates:
        db_updates["business_hours_only"] = 1 if updates["business_hours_only"] else 0
    if "followups_autopilot" in updates:
        db_updates["followups_autopilot"] = 1 if updates["followups_autopilot"] else 0
    if "followups_hour" in updates:
        db_updates["followups_hour"] = int(updates["followups_hour"])
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
        today_iso = dt.date.today().isoformat()
        ap_row = con.execute(
            "SELECT fired_at, total_queued, status FROM autopilot_runs "
            "WHERE fired_date = ? ORDER BY id DESC LIMIT 1",
            (today_iso,),
        ).fetchone()
        today_run = (
            AutopilotTodayRun(
                fired_at=ap_row["fired_at"],
                total_queued=int(ap_row["total_queued"] or 0),
                status=ap_row["status"],
            )
            if ap_row
            else None
        )
        return SafetyState(
            daily_sent_count=r["daily_sent_count"],
            daily_sent_date=r["daily_sent_date"],
            last_send_at=r["last_send_at"],
            consecutive_failures=r["consecutive_failures"],
            warning_paused_until=r["warning_paused_until"],
            autopilot_enabled=bool(r["autopilot_enabled"]),
            autopilot_hour=r["autopilot_hour"],
            autopilot_minute=(
                r["autopilot_minute"] if "autopilot_minute" in r.keys() else 0
            ) or 0,
            autopilot_count=(
                r["autopilot_count"] if "autopilot_count" in r.keys() else None
            ),
            # Older DBs that pre-date the TZ column will still return None
            # via the keys(); default to empty string so pydantic doesn't
            # 500 until the schema catches up.
            autopilot_tz=(r["autopilot_tz"] if "autopilot_tz" in r.keys() else "") or "",
            business_hours_only=bool(
                r["business_hours_only"] if "business_hours_only" in r.keys() else 0
            ),
            safety_mode=r["safety_mode"],
            autopilot_today=today_run,
            followups_autopilot=bool(
                r["followups_autopilot"] if "followups_autopilot" in r.keys() else 0
            ),
            followups_hour=(
                r["followups_hour"] if "followups_hour" in r.keys() else 11
            ) or 11,
        )


@router.post("/autopilot/reset-today")
def reset_autopilot_today():
    """Clear today's autopilot run so the next tick can re-fire the daily
    batch. Useful when the user changes the scheduled time AFTER the run
    already happened, or wants to manually re-trigger for the same day."""
    today_iso = dt.date.today().isoformat()
    with connect() as con:
        cur = con.execute(
            "DELETE FROM autopilot_runs WHERE fired_date = ?", (today_iso,)
        )
        deleted = cur.rowcount
        _log_event(con, "autopilot_reset", meta={"deleted": deleted})
        con.commit()
    # Also clear the in-process guard so _autopilot_tick re-evaluates.
    _autopilot_state["last_fired_date"] = None
    return {"ok": True, "deleted": deleted}


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
    # Accept any non-empty post_url OR an empty string (will be rejected
    # with a cleaner error downstream instead of a 422 validation blob).
    # Extra unknown fields are ignored — future extension versions can add
    # richer payloads without breaking the contract.
    model_config = {"extra": "ignore"}

    post_url: str = Field(default="")
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
    # Optional extension-generated draft fields. If the Chrome extension ran
    # Claude locally (Auto-generate email on save), we accept its output so
    # the user doesn't have to regenerate on the dashboard. Absent means the
    # lead arrives as 'New' and needs a server-side Generate click.
    gen_subject: Optional[str] = None
    gen_body: Optional[str] = None
    email_mode: Optional[str] = None      # individual | company
    cv_cluster: Optional[str] = None      # python | ml | ai_llm | fullstack | scraping | n8n | default
    # Extension per-row quick-tag: stream the scanner's 🟢/🟡/🔴 pick
    # straight into the dashboard so the user doesn't have to re-tag
    # after save.
    call_status: Optional[str] = None     # green | yellow | red | ""
    should_skip: Optional[bool] = None
    skip_reason: Optional[str] = None

    @field_validator("tags", "tech_stack", mode="before")
    @classmethod
    def _join_list(cls, v):
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x is not None and str(x).strip())
        return v

    @field_validator("should_skip", mode="before")
    @classmethod
    def _coerce_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "y"}
        return v


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
    """Insert or update by post_url. Returns (lead_id, action).
    Blocked by recyclebin dedup and company/domain blocklist on INSERT —
    updates to existing active rows always pass through.

    If the extension pre-generated a draft (gen_subject + gen_body), the new
    lead lands as status='Drafted'. If Claude decided should_skip=true, the
    caller archives it in the same transaction."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    row = con.execute(
        "SELECT id, email, status FROM leads WHERE post_url = ?", (p.post_url,)
    ).fetchone()

    has_draft = bool((p.gen_subject or "").strip() and (p.gen_body or "").strip())
    initial_status = "Drafted" if has_draft else "New"

    if row is None:
        # Already archived? Don't re-ingest — would undo a deliberate archive.
        # One UNION query covers both sources: recyclebin (restorable with
        # full payload) and archived_urls (shadow dedup that survives a
        # recyclebin clear).
        dup = con.execute(
            "SELECT 1 FROM recyclebin WHERE post_url = ? "
            "UNION ALL "
            "SELECT 1 FROM archived_urls WHERE post_url = ? "
            "LIMIT 1",
            (p.post_url, p.post_url),
        ).fetchone()
        if dup is not None:
            return -1, "recyclebin_dup"
        # Company/email-domain blocklist — skip ingest.
        block = extras.is_blocked(p.company, p.email)
        if block is not None:
            return -1, f"blocked:{block['kind']}"
        cs = (p.call_status or "").strip().lower()
        if cs not in ("green", "yellow", "red"):
            cs = None
        reviewed_at = now if cs else None
        cur = con.execute(
            """INSERT INTO leads (post_url, posted_by, company, role, tech_stack,
               rate, location, tags, post_text, email, phone,
               gen_subject, gen_body, email_mode, cv_cluster,
               skip_reason, skip_source, status,
               call_status, reviewed_at,
               first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                p.post_url, p.posted_by, p.company, p.role, p.tech_stack,
                p.rate, p.location, p.tags, p.post_text, p.email, p.phone,
                p.gen_subject or None,
                p.gen_body or None,
                p.email_mode or "individual",
                p.cv_cluster or None,
                (p.skip_reason or None) if p.should_skip else None,
                "claude" if p.should_skip else None,
                initial_status,
                cs, reviewed_at,
                now, now,
            ),
        )
        return cur.lastrowid, "inserted"

    lead_id = row["id"]
    # Refresh last_seen + fill in any now-known fields without clobbering
    # manually-edited ones. Email is only overwritten if we previously had
    # nothing. Draft fields only fill in if the server hasn't already
    # drafted this lead.
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
    if has_draft and row["status"] == "New":
        fields["gen_subject"] = p.gen_subject
        fields["gen_body"] = p.gen_body
        fields["email_mode"] = p.email_mode or "individual"
        fields["cv_cluster"] = p.cv_cluster

    sets = ", ".join(f"{k} = COALESCE(?, {k})" for k in fields)
    vals = [*fields.values(), lead_id]
    con.execute(f"UPDATE leads SET {sets} WHERE id = ?", vals)
    # Bump status to Drafted if we just filled in a draft on a 'New' row.
    if has_draft and row["status"] == "New":
        con.execute(
            "UPDATE leads SET status = 'Drafted' WHERE id = ?", (lead_id,)
        )
    return lead_id, "updated"


@router.post("/ingest")
def ingest(
    payload: IngestBatch,
    x_ext_key: Optional[str] = Header(default=None, alias="X-Ext-Key"),
):
    _require_ext_key(x_ext_key)
    inserted = 0
    updated = 0
    dup_bin = 0
    blocked = 0
    auto_skipped = 0
    missing_url = 0
    # Per-lead result so extension can map scan cards -> lead ids and
    # offer post-save actions (call_status toggle, etc.).
    items: list[dict] = []
    with connect() as con:
        for p in payload.leads:
            if not (p.post_url or "").strip():
                missing_url += 1
                items.append({"post_url": p.post_url, "action": "missing_url", "lead_id": None})
                continue
            lead_id, action = _upsert_lead(con, p)
            items.append({
                "post_url": p.post_url,
                "email": p.email,
                "action": action,
                "lead_id": lead_id if lead_id > 0 else None,
            })
            if action == "inserted":
                inserted += 1
                # Claude (from extension) already flagged this post as unfit
                # → auto-archive right after insert, same as server-side
                # draft flow does in /drafts/{id}/generate.
                if p.should_skip and lead_id > 0:
                    _archive_lead(con, lead_id,
                                  reason=f"auto_skip:{(p.skip_reason or 'claude').strip()}")
                    auto_skipped += 1
                elif lead_id > 0:
                    _rescore(con, lead_id)
            elif action == "updated":
                updated += 1
                if lead_id > 0:
                    _rescore(con, lead_id)
            elif action == "recyclebin_dup":
                dup_bin += 1
            elif action.startswith("blocked:"):
                blocked += 1
        _log_event(con, "ingest", meta={
            "inserted": inserted, "updated": updated,
            "dup_bin": dup_bin, "blocked": blocked,
            "auto_skipped": auto_skipped, "missing_url": missing_url,
        })
        con.commit()
    return {
        "inserted": inserted, "updated": updated,
        "dup_bin": dup_bin, "blocked": blocked,
        "auto_skipped": auto_skipped,
        "missing_url": missing_url,
        "total": len(payload.leads),
        "items": items,
    }


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


@router.get("/leads/{lead_id:int}")
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
    call_status: Optional[str] = None    # green | yellow | red | "" (clears)
    # Inline email correction: drafter / scraper sometimes captures a
    # malformed address (e.g. "abhishek@jigya..com"). Letting Jaydip fix
    # it inline avoids re-running the whole pipeline.
    email: Optional[str] = None
    phone: Optional[str] = None


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
            "SELECT id, reviewed_at, status, replied_at FROM leads WHERE id = ?",
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
        con.execute(f"UPDATE leads SET {sets} WHERE id = ?", [*updates.values(), lead_id])
        if auto_replied:
            _log_event(con, "manual_reply", lead_id=lead_id,
                       meta={"source": "call_status_or_note",
                             "call_status": updates.get("call_status"),
                             "note": (updates.get("jaydip_note") or "")[:120]})
        # If a draft was edited, ensure status reflects Drafted at minimum.
        if "gen_subject" in updates or "gen_body" in updates:
            con.execute(
                "UPDATE leads SET status = 'Drafted' "
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


class BulkLeadIdsBody(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    reason: Optional[str] = None


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


class BulkSnoozeBody(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    remind_at: str = Field(min_length=2, max_length=40)


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
            f"UPDATE leads SET remind_at = ?, needs_attention = 0 "
            f"WHERE id IN ({placeholders})",
            (when, *payload.ids),
        )
        _log_event(con, "bulk_snooze",
                   meta={"count": cur.rowcount, "remind_at": when})
        con.commit()
    return {"snoozed": cur.rowcount, "remind_at": when}


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
        # If this post_url was previously "cleared" to the shadow table,
        # drop that entry too — the user has changed their mind.
        post_url = data.get("post_url")
        if post_url:
            con.execute(
                "DELETE FROM archived_urls WHERE post_url = ?", (post_url,)
            )
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
    except BridgeUnreachable as e:
        # Bridge offline. Refuse to draft — a regex-only fallback would risk
        # archiving real leads. Leave the lead at its current status so the
        # user can retry after bringing the Bridge back up.
        raise HTTPException(
            503,
            f"Claude Bridge offline — cannot generate drafts without it. "
            f"Start the Bridge (Bridge online header button) and retry. "
            f"Detail: {e}",
        )
    except BridgeParseError as e:
        raise HTTPException(502, f"Bridge returned unparseable output — retry: {e}")
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

        # If fallback returned no draft (Bridge down + no regex skip hit),
        # keep status=New so the next generate attempt re-runs cleanly.
        new_status = "Drafted" if (result.subject or result.body) else "New"
        con.execute(
            "UPDATE leads SET gen_subject = ?, gen_body = ?, email_mode = ?, "
            "cv_cluster = ?, status = ?, skip_reason = NULL, "
            "skip_source = NULL WHERE id = ?",
            (
                result.subject, result.body, result.email_mode,
                result.cv_cluster, new_status, lead_id,
            ),
        )
        _log_event(con, "draft" if new_status == "Drafted" else "draft_fallback",
                   lead_id=lead_id,
                   meta={"mode": result.email_mode, "cv": result.cv_cluster})
        con.commit()

    return {
        "status": "drafted",
        "subject": result.subject,
        "body": result.body,
        "email_mode": result.email_mode,
        "cv_cluster": result.cv_cluster,
    }


# ---------- Batch draft generation (concurrent) ----------


_drafts_lock = threading.Lock()
_drafts_state: dict = {
    "running": False,
    "total": 0,
    "drafted": 0,
    "skipped": 0,
    "failed": 0,
    "started_at": None,
    "finished_at": None,
    "last_error": None,
}

DRAFT_WORKERS = 4


class DraftBatchIn(BaseModel):
    max: int = Field(default=100, ge=1, le=500)


_batch_context_lock = threading.Lock()
_batch_context: dict = {
    # Rolling list of the last ~6 drafts (compact variety dicts). Appended
    # to as each worker completes; read before each call so a parallel
    # worker can still benefit from peers that finished while it was
    # waiting.
    "prior_drafts": [],
    "prior_plans": [],
    # Cached outreach-stats snapshot for this batch. Recomputed on batch
    # start so the drafter doesn't pay the query cost per lead.
    "stats": None,
}


def _generate_one(lead_id: int) -> str:
    """Generate and persist one lead's draft. Returns 'drafted' | 'skipped' | 'failed'."""
    with connect() as con:
        row = con.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None or row["status"] != "New":
        return "skipped"

    # Snapshot the shared batch context so concurrent writers don't surprise
    # us mid-call. Read-copy is fine — we never mutate these lists in place.
    with _batch_context_lock:
        prior_drafts = list(_batch_context["prior_drafts"])
        prior_plans = list(_batch_context["prior_plans"])
        stats = _batch_context["stats"]

    try:
        result = _claude_generate(
            posted_by=row["posted_by"] or "",
            company=row["company"] or "",
            role=row["role"] or "",
            tech_stack=row["tech_stack"] or "",
            location=row["location"] or "",
            post_text=row["post_text"] or "",
            prior_drafts=prior_drafts,
            prior_plans=prior_plans,
            stats=stats,
        )
    except BridgeUnreachable as e:
        # Bridge dropped mid-batch. Leave the lead at status=New and bubble
        # the specific reason so the worker's shared state can tell the UI
        # "Bridge offline, N leads skipped" — no data mutation, safe retry.
        with connect() as con:
            _log_event(con, "draft_bridge_offline",
                       lead_id=lead_id, meta={"error": str(e)[:200]})
            con.commit()
        return "bridge_offline"
    except BridgeParseError as e:
        with connect() as con:
            _log_event(con, "draft_parse_error",
                       lead_id=lead_id, meta={"error": str(e)[:200]})
            con.commit()
        return "failed"
    except Exception as e:
        with connect() as con:
            _log_event(con, "draft_error", lead_id=lead_id, meta={"error": str(e)[:200]})
            con.commit()
        return "failed"

    with connect() as con:
        if result.should_skip:
            con.execute(
                "UPDATE leads SET gen_subject = ?, gen_body = ?, email_mode = ?, "
                "cv_cluster = ?, skip_reason = ?, skip_source = ?, "
                "status = 'Skipped' WHERE id = ?",
                (result.subject, result.body, result.email_mode,
                 result.cv_cluster, result.skip_reason, result.skip_source, lead_id),
            )
            _log_event(con, "draft_skipped", lead_id=lead_id,
                       meta={"reason": result.skip_reason})
            _archive_lead(con, lead_id, reason=f"auto_skip:{result.skip_reason}")
            con.commit()
            return "skipped"

        # Claude produced a non-skip verdict but an empty body — rare, but
        # we want this loud, not silently treated as "drafted". Mark failed
        # so the user can retry rather than ship an empty mail.
        if not (result.subject and result.body):
            with connect() as con:
                _log_event(con, "draft_empty_result",
                           lead_id=lead_id,
                           meta={"mode": result.email_mode})
                con.commit()
            return "failed"

        con.execute(
            "UPDATE leads SET gen_subject = ?, gen_body = ?, email_mode = ?, "
            "cv_cluster = ?, status = 'Drafted', skip_reason = NULL, "
            "skip_source = NULL WHERE id = ?",
            (result.subject, result.body, result.email_mode,
             result.cv_cluster, lead_id),
        )
        _log_event(con, "draft", lead_id=lead_id,
                   meta={"mode": result.email_mode, "cv": result.cv_cluster})
        con.commit()

    # Record this draft into the batch-shared context so subsequent workers
    # in the same batch see it and explicitly vary their hook/opening/case
    # study. Bounded ring — we only need the last ~6 for Claude's context.
    with _batch_context_lock:
        _batch_context["prior_drafts"].append(draft_variety_key(result))
        _batch_context["prior_drafts"] = _batch_context["prior_drafts"][-6:]
        if result.plan:
            _batch_context["prior_plans"].append(result.plan)
            _batch_context["prior_plans"] = _batch_context["prior_plans"][-6:]

    return "drafted"


def _drafts_worker(lead_ids: list[int]) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    bridge_offline_count = 0
    try:
        with ThreadPoolExecutor(max_workers=DRAFT_WORKERS) as pool:
            futures = {pool.submit(_generate_one, lid): lid for lid in lead_ids}
            for fut in as_completed(futures):
                try:
                    outcome = fut.result()
                except Exception as e:
                    outcome = "failed"
                    _drafts_state["last_error"] = str(e)[:200]
                if outcome == "drafted":
                    _drafts_state["drafted"] += 1
                elif outcome == "skipped":
                    _drafts_state["skipped"] += 1
                elif outcome == "bridge_offline":
                    bridge_offline_count += 1
                    _drafts_state["failed"] += 1
                    _drafts_state["last_error"] = (
                        f"Claude Bridge went offline — {bridge_offline_count} lead(s) "
                        "skipped without mutation. Start the Bridge and retry."
                    )
                else:
                    _drafts_state["failed"] += 1
    finally:
        _drafts_state["running"] = False
        _drafts_state["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")


@router.post("/drafts/generate/batch")
def generate_drafts_batch(payload: DraftBatchIn):
    with _drafts_lock:
        if _drafts_state["running"]:
            raise HTTPException(409, "A draft batch is already running")
        # Preflight Bridge health. Refusing at the door is far safer than
        # spawning a worker that would refuse every lead and look like the
        # batch "crashed". A single-shot probe (~1.5s) keeps the latency
        # unnoticeable when the Bridge IS up.
        if not bridge_is_up():
            raise HTTPException(
                503,
                "Claude Bridge offline — cannot start a draft batch. "
                "Click 'Bridge online' in the header to launch it, then retry.",
            )

        with connect() as con:
            rows = con.execute(
                "SELECT id FROM leads "
                "WHERE status = 'New' "
                "  AND post_text IS NOT NULL AND TRIM(post_text) != '' "
                "ORDER BY first_seen_at ASC LIMIT ?",
                (payload.max,),
            ).fetchall()
            lead_ids = [r["id"] for r in rows]
            if not lead_ids:
                raise HTTPException(400, "No 'New' leads to draft")
            _log_event(con, "drafts_batch_start", meta={"count": len(lead_ids)})
            con.commit()

        _drafts_state.update({
            "running": True,
            "total": len(lead_ids),
            "drafted": 0,
            "skipped": 0,
            "failed": 0,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "last_error": None,
        })
        # Fresh batch → wipe the rolling variety window and seed the stats
        # snapshot once so every worker shares the same "avoid" hints.
        # Stats failure is silently ignored — empty hints just means the
        # drafter falls back to rule-only guidance.
        with _batch_context_lock:
            _batch_context["prior_drafts"] = []
            _batch_context["prior_plans"] = []
            try:
                _batch_context["stats"] = extras.outreach_stats()
            except Exception:
                _batch_context["stats"] = None
        threading.Thread(target=_drafts_worker, args=(lead_ids,), daemon=True).start()

    return {"started": True, "total": len(lead_ids)}


@router.get("/drafts/generate/status")
def drafts_batch_status():
    return dict(_drafts_state)


# ---------- Gmail connect / test / disconnect ----------


class GmailConnectIn(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    app_password: str = Field(min_length=10, max_length=32)
    display_name: Optional[str] = None
    daily_cap: Optional[int] = None


class GmailCapIn(BaseModel):
    daily_cap: int = Field(ge=1, le=500)


@router.get("/gmail/status")
def gmail_status():
    """Backward-compat single-account summary. Reflects the first active
    account so older UI keeps working. New UI uses /gmail/accounts."""
    accounts = gmail.list_accounts()
    active = [a for a in accounts if a["status"] == "active"]
    head = active[0] if active else (accounts[0] if accounts else None)
    if not head:
        return {"connected": False, "email": None, "connected_at": None,
                "total_accounts": 0, "active_accounts": 0}
    return {
        "connected": True,
        "email": head["email"],
        "connected_at": head["connected_at"],
        "last_verified_at": head["last_verified_at"],
        "total_accounts": len(accounts),
        "active_accounts": len(active),
    }


@router.get("/gmail/accounts")
def gmail_list_accounts():
    accounts = gmail.list_accounts()
    total_sent_today = sum(a["sent_today"] for a in accounts)
    total_cap = sum(a["daily_cap"] for a in accounts
                    if a["status"] == "active")
    return {
        "rows": accounts,
        "total_sent_today": total_sent_today,
        "total_daily_cap": total_cap,
    }


@router.post("/gmail/connect")
def gmail_connect(payload: GmailConnectIn):
    """Adds a new Gmail account OR updates the password on an existing one
    (matched by email). Runs SMTP+IMAP verification first."""
    try:
        check = gmail.verify_credentials(
            payload.email.strip(), payload.app_password.strip()
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    acc_id = gmail.save_credentials(
        payload.email.strip(), payload.app_password.strip(),
        display_name=(payload.display_name or "").strip() or None,
    )
    if payload.daily_cap is not None:
        gmail.set_account_cap(acc_id, payload.daily_cap)
    with connect() as con:
        _log_event(con, "gmail_connect",
                   meta={"email": payload.email, "account_id": acc_id, **check})
        con.commit()
    return {"connected": True, "email": payload.email,
            "account_id": acc_id, **check}


@router.post("/gmail/accounts/{account_id:int}/pause")
def gmail_pause_account(account_id: int):
    gmail.set_account_status(account_id, "paused")
    with connect() as con:
        _log_event(con, "gmail_pause", meta={"account_id": account_id})
        con.commit()
    return {"ok": True, "account_id": account_id, "status": "paused"}


@router.post("/gmail/accounts/{account_id:int}/resume")
def gmail_resume_account(account_id: int):
    gmail.set_account_status(account_id, "active")
    with connect() as con:
        _log_event(con, "gmail_resume", meta={"account_id": account_id})
        con.commit()
    return {"ok": True, "account_id": account_id, "status": "active"}


@router.post("/gmail/accounts/{account_id:int}/cap")
def gmail_set_cap(account_id: int, payload: GmailCapIn):
    gmail.set_account_cap(account_id, payload.daily_cap)
    return {"ok": True, "account_id": account_id, "daily_cap": payload.daily_cap}


class GmailWarmupIn(BaseModel):
    enabled: bool
    reset_start: bool = False


class WarmupCurveIn(BaseModel):
    # Each stage: send up to `cap` per day until day `days` (exclusive).
    # List must cover the ramp — the last stage's cap applies until daily_cap
    # caps in. Example: [[1,5],[3,10],[7,20],[14,35]] = 5/day on day 0,
    # 10/day days 1-2, 20/day days 3-6, 35/day days 7-13, full cap day 14+.
    stages: list[list[int]] = Field(min_length=1, max_length=10)


@router.get("/gmail/warmup/curve")
def get_warmup_curve_ep():
    curve = gmail.get_warmup_curve()
    return {
        "stages": [[d, c] for d, c in curve],
        "default": [[d, c] for d, c in gmail.DEFAULT_WARMUP_CURVE],
    }


@router.post("/gmail/warmup/curve")
def set_warmup_curve_ep(payload: WarmupCurveIn):
    try:
        tuples = [(int(s[0]), int(s[1])) for s in payload.stages
                  if len(s) == 2]
    except Exception:
        raise HTTPException(400, "Each stage must be [days, cap] ints")
    if not tuples:
        raise HTTPException(400, "At least one stage required")
    try:
        gmail.save_warmup_curve(tuples)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with connect() as con:
        _log_event(con, "warmup_curve_update",
                   meta={"stages": [[d, c] for d, c in tuples]})
        con.commit()
    return {"ok": True, "stages": [[d, c] for d, c in tuples]}


@router.post("/gmail/accounts/{account_id:int}/warmup")
def gmail_set_warmup(account_id: int, payload: GmailWarmupIn):
    gmail.set_account_warmup(account_id, payload.enabled, payload.reset_start)
    with connect() as con:
        _log_event(con, "gmail_warmup",
                   meta={"account_id": account_id, "enabled": payload.enabled,
                         "reset_start": payload.reset_start})
        con.commit()
    return {"ok": True, "account_id": account_id,
            "warmup_enabled": payload.enabled}


@router.delete("/gmail/accounts/{account_id:int}")
def gmail_remove_account(account_id: int):
    gmail.remove_account(account_id)
    with connect() as con:
        _log_event(con, "gmail_remove", meta={"account_id": account_id})
        con.commit()
    return {"ok": True, "account_id": account_id}


@router.post("/gmail/test")
def gmail_test(account_id: Optional[int] = None):
    """Verify creds for a given account (or the first active if omitted)."""
    if account_id is not None:
        creds = gmail.get_account_creds(account_id)
        acc_id = account_id
    else:
        creds = gmail.get_credentials()
        accounts = gmail.list_accounts()
        acc_id = next((a["id"] for a in accounts if a["status"] == "active"), None)
    if not creds:
        raise HTTPException(400, "Gmail not connected")
    try:
        check = gmail.verify_credentials(*creds)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    now = dt.datetime.now().isoformat(timespec="seconds")
    if acc_id is not None:
        with connect() as con:
            con.execute(
                "UPDATE gmail_accounts SET last_verified_at = ? WHERE id = ?",
                (now, acc_id),
            )
            con.commit()
    return {"ok": True, "account_id": acc_id, **check, "last_verified_at": now}




# ---------- safety gate ----------


def _effective_daily_cap(con) -> int:
    """Global cap = sum of active Gmail accounts' warmup-aware effective caps,
    bounded below by DAILY_CAP.

    Previously this summed raw daily_cap and ignored the warmup curve, so a
    fresh account at daily_cap=25 contributed 25 to the global cap even
    though the picker would only let 15 through while it ramped. That
    caused the safety rail to allow more sends than the per-account caps
    could absorb, stalling batches mid-flight. Using effective_cap keeps
    both numbers in agreement.
    """
    rows = con.execute(
        "SELECT daily_cap, warmup_enabled, warmup_start_date, connected_at "
        "FROM gmail_accounts WHERE status = 'active'"
    ).fetchall()
    curve = gmail.get_warmup_curve()
    total = 0
    for r in rows:
        total += gmail.effective_cap(
            int(r["daily_cap"]),
            bool(r["warmup_enabled"]),
            r["warmup_start_date"] or r["connected_at"],
            curve=curve,
        )
    return max(DAILY_CAP, total)


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

    cap = _effective_daily_cap(con)
    if s["daily_sent_count"] >= cap:
        raise HTTPException(
            429, f"Daily cap of {cap} already reached"
        )

    if not allow_quiet_hours:
        now = dt.datetime.now()
        strict = bool(s["business_hours_only"]) if "business_hours_only" in s.keys() else False
        if strict:
            # Strict B2B schedule: Mon-Fri, 09-18 local. Weekends or
            # before/after hours → refuse.
            if now.weekday() >= 5:
                raise HTTPException(
                    423, "Business-hours-only mode: no sends on weekends",
                )
            if now.hour < 9 or now.hour >= 18:
                raise HTTPException(
                    423, "Business-hours-only mode: sends allowed 09:00–18:00 local",
                )
        else:
            # Loose default: just avoid the obvious bot window.
            if now.hour >= 23 or now.hour < 7:
                raise HTTPException(
                    423, "Quiet hours active (23:00–07:00 local)",
                )


# 1x1 transparent GIF, served on every /t/open/*.gif hit.
_TRACKING_PIXEL_BYTES = bytes.fromhex(
    "47494638396101000100800000ffffff00000021f90401000000002c0000000001000100000202044c01003b"
)


@router.get("/t/open/{token}.gif")
def tracking_pixel(token: str, request: Request):
    """Public tracking beacon. Logs an open against the lead whose
    open_token matches. Always returns a 1x1 GIF — even on unknown tokens
    or any failure — so broken recipients never see a broken image."""
    try:
        ua = request.headers.get("user-agent", "")[:200]
        client = request.client.host if request.client else None
        with connect() as con:
            row = con.execute(
                "SELECT id FROM leads WHERE open_token = ?", (token,),
            ).fetchone()
            if row:
                now = dt.datetime.now().isoformat(timespec="seconds")
                con.execute(
                    "INSERT INTO email_opens (lead_id, opened_at, user_agent, ip) "
                    "VALUES (?, ?, ?, ?)",
                    (row["id"], now, ua, client),
                )
                con.execute(
                    "UPDATE leads SET open_count = COALESCE(open_count, 0) + 1, "
                    "first_opened_at = COALESCE(first_opened_at, ?), "
                    "last_opened_at = ? WHERE id = ?",
                    (now, now, row["id"]),
                )
                _log_event(con, "email_open", lead_id=row["id"],
                           meta={"ua": ua[:100]})
                con.commit()
    except Exception as e:
        print(f"[tracking] open log failed: {e}")
    return Response(
        content=_TRACKING_PIXEL_BYTES,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


def _rescore(con, lead_id: int) -> None:
    """Recompute fit_score for a lead and persist. Call after any field
    change that affects scoring (email set, draft generated, company
    added). Cheap — pure Python regex, no I/O."""
    row = con.execute(
        "SELECT email, role, tech_stack, company, phone, gen_subject, "
        "gen_body, posted_by, post_url, first_seen_at "
        "FROM leads WHERE id = ?", (lead_id,),
    ).fetchone()
    if row is None:
        return
    score, reasons = linkedin_scoring.compute_score(dict(row))
    con.execute(
        "UPDATE leads SET fit_score = ?, fit_score_reasons = ? WHERE id = ?",
        (score, json.dumps(reasons), lead_id),
    )


def _ensure_open_token(con, lead_id: int) -> str:
    """Return the lead's open_token, generating one if missing. Called
    just before send so every outgoing email has a tracking URL."""
    import secrets
    row = con.execute(
        "SELECT open_token FROM leads WHERE id = ?", (lead_id,)
    ).fetchone()
    if row and row["open_token"]:
        return row["open_token"]
    token = secrets.token_urlsafe(22)
    con.execute(
        "UPDATE leads SET open_token = ? WHERE id = ?",
        (token, lead_id),
    )
    return token


import os
import io
import zipfile
from pathlib import Path as _Path
from fastapi.responses import StreamingResponse

# Public URL the recipient's mail client will hit to load the tracking
# pixel. MUST be reachable from the open internet (Cloudflare Tunnel /
# ngrok / deployed backend). Override via env var in production.
TRACKING_BASE_URL = os.environ.get(
    "LINKEDIN_TRACKING_BASE_URL", "http://localhost:8900"
).rstrip("/")


# ---------- Extension zip download ----------


_EXT_DIR = _Path(__file__).resolve().parent.parent.parent / "linkedin_extension"


@router.get("/extension/download")
def download_extension():
    """Zip the linkedin_extension/ folder on the fly and stream as a
    download. Lets a user on any device grab the extension without
    needing Git or the Windows file path."""
    if not _EXT_DIR.is_dir():
        raise HTTPException(500, f"Extension folder not found at {_EXT_DIR}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in _EXT_DIR.rglob("*"):
            if fp.is_file():
                # Skip the local .zip itself, node_modules-style junk, and
                # OS-generated files.
                name = fp.name.lower()
                if name.endswith(".zip") or name in {".ds_store", "thumbs.db"}:
                    continue
                zf.write(fp, fp.relative_to(_EXT_DIR.parent))
    buf.seek(0)
    today = dt.date.today().isoformat()
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="linkedin_extension_{today}.zip"',
            "Cache-Control": "no-store",
        },
    )


def _tracking_is_public() -> bool:
    """Pixel is only embedded when TRACKING_BASE_URL points at a host the
    recipient's mail client can actually reach. Localhost / 127.* / RFC1918
    addresses never will, so we skip the pixel entirely in dev to avoid
    shipping a broken <img> tag (which is both useless and a minor spam
    signal). Set LINKEDIN_TRACKING_BASE_URL to your public tunnel / domain
    before sending real mail if you want open tracking."""
    host = TRACKING_BASE_URL.lower()
    if not host.startswith(("http://", "https://")):
        return False
    bad = ("localhost", "127.0.0.1", "0.0.0.0", "::1",
           "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
           "172.2", "172.30.", "172.31.")
    stripped = host.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    return not any(stripped == b or stripped.startswith(b) for b in bad)


def _tracking_pixel_url(token: str) -> str | None:
    if not _tracking_is_public():
        return None
    return f"{TRACKING_BASE_URL}/api/linkedin/t/open/{token}.gif"


def _record_send(con, lead_id: int, message_id: str, sent_at: str,
                 account_id: int | None = None) -> None:
    con.execute(
        "UPDATE leads SET status = 'Sent', sent_at = ?, sent_message_id = ?, "
        "sent_via_account_id = ? WHERE id = ?",
        (sent_at, message_id, account_id, lead_id),
    )
    con.execute(
        "UPDATE safety_state SET daily_sent_count = daily_sent_count + 1, "
        "last_send_at = ?, consecutive_failures = 0 WHERE id = 1",
        (sent_at,),
    )
    _log_event(con, "send", lead_id=lead_id,
               meta={"msg_id": message_id, "account_id": account_id})


def _record_failure(con, lead_id: int, err: str) -> None:
    con.execute(
        "UPDATE safety_state SET consecutive_failures = consecutive_failures + 1 "
        "WHERE id = 1"
    )
    _log_event(con, "send_error", lead_id=lead_id, meta={"error": err[:400]})


# ---------- Reply inbox + threaded response ----------


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


class DraftReplyBody(BaseModel):
    # User-typed direction for this specific reply. Claude blends the
    # instruction into the tone/content instead of ignoring it. Optional —
    # an empty value falls back to the generic drafter.
    hint: Optional[str] = Field(default=None, max_length=1000)


def _recent_style_examples(con, limit: int = 5) -> list[dict]:
    """Last N outbound replies Jaydip actually sent (captured as
    reply_sent events), paired with the inbound that prompted them. Used
    as few-shot style guidance for Claude so the drafter gradually picks
    up whatever wording Jaydip keeps reaching for."""
    rows = con.execute(
        """
        SELECT e.meta_json, e.at
        FROM events e
        WHERE e.kind = 'reply_sent'
        ORDER BY e.at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            m = json.loads(r["meta_json"] or "{}")
        except Exception:
            continue
        inbound = (m.get("inbound_snippet") or "").strip()
        outbound = (m.get("outbound_body") or "").strip()
        if inbound and outbound:
            out.append({"inbound": inbound[:400], "outbound": outbound[:800]})
    return out


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


def _first_name_from_posted_by(raw: str) -> str:
    s = (raw or "").strip().split()
    return s[0].capitalize() if s else ""


class SendReplyBody(BaseModel):
    body: str = Field(min_length=5, max_length=20_000)
    subject: Optional[str] = None  # defaults to "Re: <original subject>"


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


class MarkHandledBody(BaseModel):
    handled: bool = True


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


class BulkHandleBody(BaseModel):
    reply_ids: list[int] = Field(min_length=1, max_length=500)
    handled: bool = True


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


# ---------- Send flow ----------


class ScheduleBody(BaseModel):
    scheduled_send_at: str = Field(min_length=10, max_length=40)


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
            "SELECT id, email, status FROM leads WHERE id = ?", (lead_id,),
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
            "UPDATE leads SET scheduled_send_at = ?, "
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
            "UPDATE leads SET scheduled_send_at = NULL "
            "WHERE id = ? AND scheduled_send_at IS NOT NULL",
            (lead_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Lead has no schedule")
        _log_event(con, "unscheduled", lead_id=lead_id)
        con.commit()
    return {"ok": True}


class SnoozeBody(BaseModel):
    # ISO timestamp OR a relative hint like "1d" / "3d" / "1w"
    remind_at: str = Field(min_length=2, max_length=40)


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
            "SELECT id FROM leads WHERE id = ?", (lead_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Lead not found")
        con.execute(
            "UPDATE leads SET remind_at = ?, needs_attention = 0 WHERE id = ?",
            (when, lead_id),
        )
        _log_event(con, "snoozed", lead_id=lead_id, meta={"remind_at": when})
        con.commit()
    return {"ok": True, "remind_at": when}


@router.post("/leads/{lead_id:int}/unsnooze")
def unsnooze_lead(lead_id: int):
    with connect() as con:
        cur = con.execute(
            "UPDATE leads SET remind_at = NULL, needs_attention = 1 "
            "WHERE id = ? AND remind_at IS NOT NULL",
            (lead_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Lead is not snoozed")
        _log_event(con, "unsnoozed", lead_id=lead_id)
        con.commit()
    return {"ok": True}


_OOO_NUDGE_BODY = (
    "Hi {first},\n\n"
    "Hope you had a good break. Circling back on the ML Developer "
    "opportunity I pinged about earlier - still open to a quick 15-min "
    "call this week if the timing works better now?\n\n"
    "Jaydip\n"
)


def _send_ooo_nudge(lead_id: int) -> dict:
    """Send a polite threaded follow-up to a lead whose original reply
    was an OOO auto-responder. Uses the last inbound OOO msg-id so it
    nests in the same Gmail conversation. Best-effort — failures bubble
    up to the scheduler so the row stays queued for next tick."""
    with connect() as con:
        lead = con.execute(
            "SELECT id, email, posted_by, gen_subject, sent_message_id, "
            "sent_via_account_id FROM leads WHERE id = ?", (lead_id,),
        ).fetchone()
        if lead is None:
            raise RuntimeError("Lead not found")
        if not lead["email"]:
            raise RuntimeError("No email on lead")
        last = con.execute(
            "SELECT gmail_msg_id FROM replies WHERE lead_id = ? AND kind = 'reply' "
            "ORDER BY received_at DESC LIMIT 1", (lead_id,),
        ).fetchone()

    first = _first_name_from_posted_by(lead["posted_by"] or "")
    body = _OOO_NUDGE_BODY.format(first=first or "there")
    subject = f"Re: {lead['gen_subject'] or 'Following up'}"
    refs = []
    if lead["sent_message_id"]:
        refs.append(f"<{lead['sent_message_id']}>")
    if last and last["gmail_msg_id"]:
        refs.append(f"<{last['gmail_msg_id']}>")
    in_reply_to = (last["gmail_msg_id"] if last else None) or lead["sent_message_id"]
    account_id = lead["sent_via_account_id"] or gmail.pick_next_account_id()
    if account_id is None:
        raise RuntimeError("No Gmail account with remaining quota")

    result = gmail.send_email(
        to=lead["email"], subject=subject, body=body, account_id=account_id,
        in_reply_to=in_reply_to,
        references=" ".join(refs) if refs else None,
    )
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as con:
        con.execute(
            "UPDATE leads SET ooo_nudge_sent_at = ?, ooo_nudge_at = NULL "
            "WHERE id = ?", (now, lead_id),
        )
        _log_event(con, "ooo_nudge_sent", lead_id=lead_id,
                   meta={"account_id": account_id, "msg_id": result.message_id})
        con.commit()
    return {"sent_at": now, "message_id": result.message_id}


def _scheduler_tick() -> dict:
    """Run every minute: find Drafted leads whose scheduled_send_at has
    passed, send each via the standard send_one path. Returns counts."""
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as con:
        due = con.execute(
            "SELECT id FROM leads "
            "WHERE scheduled_send_at IS NOT NULL "
            "  AND scheduled_send_at <= ? "
            "  AND status IN ('Drafted', 'New') "
            "  AND (jaydip_note IS NULL OR TRIM(jaydip_note) = '') "
            "LIMIT 20",
            (now,),
        ).fetchall()
    sent = 0
    skipped = 0
    errors: list[dict] = []
    for row in due:
        lead_id = row["id"]
        try:
            send_one(lead_id)    # reuses all safety / blocklist / warmup logic
            sent += 1
        except HTTPException as e:
            # Skip scheduling attempt for this tick — keep the row
            # scheduled so we try again next minute (unless it's a
            # permanent failure the user needs to see).
            errors.append({"lead_id": lead_id, "status": e.status_code, "detail": e.detail})
            skipped += 1
        except Exception as e:
            errors.append({"lead_id": lead_id, "detail": str(e)[:200]})
            skipped += 1
        else:
            # Clear the schedule on success — status is now Sent, so the
            # row won't match our WHERE clause again anyway, but null it
            # for cleanliness.
            with connect() as con:
                con.execute(
                    "UPDATE leads SET scheduled_send_at = NULL WHERE id = ?",
                    (lead_id,),
                )
                con.commit()
    # Process due OOO nudges alongside regular scheduled sends.
    nudges_sent = 0
    nudge_errors: list[dict] = []
    with connect() as con:
        due_nudges = con.execute(
            "SELECT id FROM leads "
            "WHERE ooo_nudge_at IS NOT NULL AND ooo_nudge_at <= ? "
            "  AND ooo_nudge_sent_at IS NULL "
            "  AND (jaydip_note IS NULL OR TRIM(jaydip_note) = '') "
            "LIMIT 10", (now,),
        ).fetchall()
    for row in due_nudges:
        try:
            _send_ooo_nudge(row["id"])
            nudges_sent += 1
        except Exception as e:
            nudge_errors.append({"lead_id": row["id"], "detail": str(e)[:200]})

    return {"sent": sent, "skipped": skipped, "errors": errors,
            "due": len(due), "nudges_sent": nudges_sent,
            "nudge_errors": nudge_errors}


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
# threading.Event — worker waits on .wait(seconds) for the jitter so Stop
# takes effect on the next sleep boundary (not up to 1s later). set() also
# cheaper than polling a shared bool.
_batch_stop_event = threading.Event()
# Reference to the running worker thread so send_batch can verify the
# previous one actually terminated before starting a new one (defends
# against a freshly-crashed worker whose finally has run but whose thread
# is still unwinding).
_batch_thread: Optional[threading.Thread] = None
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
    # Upper bound is generous — real daily quota is enforced dynamically from
    # the sum of active Gmail account caps in _check_safety_before_send.
    count: int = Field(default=5, ge=1, le=500)
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
    crashed: Optional[str] = None
    try:
        for idx, lead_id in enumerate(lead_ids):
            if _batch_stop_event.is_set():
                break

            with connect() as con:
                try:
                    _check_safety_before_send(con)
                except HTTPException as e:
                    _batch_state["last_error"] = str(e.detail)
                    break

                lead = con.execute(
                    "SELECT email, gen_subject, gen_body, jaydip_note, status, "
                    "       company, cv_cluster "
                    "FROM leads WHERE id = ?", (lead_id,),
                ).fetchone()
                if lead is None or lead["status"] == "Sent" or (
                    lead["jaydip_note"] or ""
                ).strip():
                    _batch_state["skipped"] += 1
                    continue
                if extras.is_blocked(lead["company"], lead["email"]):
                    _batch_state["skipped"] += 1
                    continue
                missing_cv = extras.cv_required_but_missing(lead["cv_cluster"])
                if missing_cv:
                    # Stall — upload the matching CV first. We flag the lead
                    # so the UI surfaces it rather than quietly dropping.
                    con.execute(
                        "UPDATE leads SET needs_attention = 1 WHERE id = ?",
                        (lead_id,),
                    )
                    _log_event(
                        con, "cv_missing",
                        lead_id=lead_id,
                        meta={"cluster": missing_cv},
                    )
                    con.commit()
                    _batch_state["last_error"] = (
                        f"Skipped lead {lead_id}: no CV uploaded for "
                        f"cluster '{missing_cv}'"
                    )
                    _batch_state["skipped"] += 1
                    continue

            _batch_state["current_lead_id"] = lead_id
            _batch_state["current_email"] = lead["email"]

            attachment = extras.pick_cv_path(lead["cv_cluster"])

            picked_account_id = gmail.pick_next_account_id()
            if picked_account_id is None:
                # Could be "quota exhausted" (stop) OR "all accounts in
                # cooldown" (wait). seconds_until_next_account differentiates.
                wait_s = gmail.seconds_until_next_account()
                if wait_s is None:
                    _batch_state["last_error"] = (
                        "No Gmail account with remaining quota"
                    )
                    _batch_state["skipped"] += 1
                    break
                # Accounts have quota but are cooling down. Wait for the
                # soonest one (+ small buffer) and retry this same lead.
                wait_s = max(10, wait_s + 5)
                _batch_state["last_error"] = (
                    f"All accounts cooling down — waiting {wait_s}s"
                )
                if _batch_stop_event.wait(timeout=wait_s):
                    break
                picked_account_id = gmail.pick_next_account_id()
                if picked_account_id is None:
                    # Still nothing after the wait — treat as exhausted.
                    _batch_state["last_error"] = (
                        "Cooldown elapsed but no account is available"
                    )
                    _batch_state["skipped"] += 1
                    break
                _batch_state["last_error"] = None

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
                with connect() as con:
                    _record_send(con, lead_id, result.message_id,
                                 result.sent_at, account_id=result.account_id)
                    con.commit()
                _batch_state["sent"] += 1
            except Exception as e:
                with connect() as con:
                    _record_failure(con, lead_id, str(e))
                    con.commit()
                paused = gmail.record_send_failure(picked_account_id, str(e))
                _batch_state["failed"] += 1
                _batch_state["last_error"] = (
                    f"[acct {picked_account_id} auto-paused] {str(e)[:160]}"
                    if paused else str(e)[:200]
                )

            # Jitter between sends, but not after the final one.
            if idx < len(lead_ids) - 1 and not _batch_stop_event.is_set():
                wait = random.randint(BATCH_JITTER_MIN_S, BATCH_JITTER_MAX_S)
                # Event.wait returns True early if stop() is called — no
                # more 1-sec poll granularity.
                if _batch_stop_event.wait(timeout=wait):
                    break
    except Exception as exc:
        # Never let the batch thread die silently. Record so the UI and
        # the events log show *why* the batch stopped early, instead of
        # the user seeing a partially-sent queue with no explanation.
        crashed = f"{type(exc).__name__}: {exc}"[:300]
        print(f"[batch_worker] crashed: {crashed}")
    finally:
        _batch_state["running"] = False
        _batch_state["current_lead_id"] = None
        _batch_state["current_email"] = None
        _batch_state["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _batch_state["stop_requested"] = False
        _batch_state["source"] = source
        if crashed:
            _batch_state["last_error"] = f"Batch crashed: {crashed}"
        _batch_stop_event.clear()
        try:
            with connect() as con:
                _log_event(con, "batch_end", meta={
                    "source": source,
                    "sent": _batch_state["sent"],
                    "failed": _batch_state["failed"],
                    "skipped": _batch_state["skipped"],
                    "crashed": crashed,
                })
                con.commit()
        except Exception as log_exc:
            # Don't let a logging failure mask the original state reset.
            print(f"[batch_worker] event log failed: {log_exc}")


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


# ---------- daily digest (runs from main.py loop at 9am local) ----------

_digest_state = {"sent_date": None}


def _digest_already_sent(date_iso: str) -> bool:
    """Helper used by linkedin_extras.run_digest to short-circuit a
    duplicate send within the same day. Module-level state means the
    process restart re-allows one send (fine — preserves the "you got
    a digest after a crash" signal)."""
    return _digest_state["sent_date"] == date_iso


def _mark_digest_sent(date_iso: str) -> None:
    _digest_state["sent_date"] = date_iso


_followups_state = {"last_run_date": None}


def _followups_tick() -> None:
    """Once-per-day auto follow-up sender. Reads safety_state for the
    on/off switch and the local hour to fire at. Bounded by the same
    safety rails as manual sends — quota, cooldowns, blocklist all
    enforced in run_followups -> send_email."""
    with connect() as con:
        s = con.execute(
            "SELECT followups_autopilot, followups_hour FROM safety_state WHERE id=1"
        ).fetchone()
    if not s or not s["followups_autopilot"]:
        return
    now = dt.datetime.now()
    if now.hour < int(s["followups_hour"] or 11):
        return
    today = now.date().isoformat()
    if _followups_state["last_run_date"] == today:
        return
    try:
        import linkedin_extras as _extras
        result = _extras.run_followups(_extras.FollowupRunIn(dry_run=False))
        with connect() as con:
            _log_event(con, "followups_autopilot_run", meta={
                "sent": result.get("sent", 0),
                "skipped": result.get("skipped", 0),
                "errors": len(result.get("errors", []) or []),
            })
            con.commit()
    except HTTPException:
        # Gmail not connected, blocked by safety, etc — log via the
        # extras path that already records skips. Mark date so we don't
        # spam retries every minute.
        pass
    except Exception:
        # Any other failure: don't mark sent so we'll retry next tick.
        return
    _followups_state["last_run_date"] = today


def _digest_tick() -> None:
    """Fire the daily digest once per day at/after 9am local. Called by
    the linkedin_poll_loop in main.py. The tick itself is idempotent
    (run_digest checks _digest_already_sent), so even at minute-1
    precision we never double-fire.

    Off by default — set LINKEDIN_DIGEST_ENABLED=1 to receive the daily
    summary email. Disabled per user request: the digest landed in their
    own outreach inbox and was visual noise.
    """
    if os.environ.get("LINKEDIN_DIGEST_ENABLED", "0") != "1":
        return
    now = dt.datetime.now()
    if now.hour < 9:
        return
    today = now.date().isoformat()
    if _digest_state["sent_date"] == today:
        return
    try:
        # Lazy import — extras imports linkedin_api at module level, so
        # a top-level import here would create a circular dependency.
        import linkedin_extras
        linkedin_extras.run_digest(force=False)
    except HTTPException as e:
        # 503 means "no recipient yet" (no Gmail connected) — that's a
        # user-config gap, not a code bug. Mark date so we don't keep
        # retrying every minute.
        if e.status_code == 503:
            _digest_state["sent_date"] = today
    except Exception:
        # Any other failure: don't mark sent so we'll retry next tick.
        pass


# ---------- stale-draft sweep (runs hourly via main.py loop) ----------

# How many days of "Drafted, never sent, never scheduled" before we move
# the lead to the recyclebin. 14d is the right side of conservative — most
# real prospects don't sit drafted that long, and if they did the post
# context is stale enough that the email reads dated.
STALE_DRAFT_DAYS = 14

_stale_sweep_state = {"last_run_date": None}


def _stale_drafts_sweep() -> int:
    """Archive Drafted leads that have been idle for >STALE_DRAFT_DAYS.

    Idle = `events.draft` event timestamp older than the threshold AND no
    schedule pending AND no manual review touch (jaydip_note empty,
    reviewed_at NULL). Anything the user has actively touched stays put;
    only the truly forgotten rows get swept.

    Idempotent and bounded — designed for an hourly tick. Returns the
    number of leads moved this run, mostly for log visibility."""
    today = dt.date.today().isoformat()
    if _stale_sweep_state["last_run_date"] == today:
        return 0
    cutoff = (dt.date.today() - dt.timedelta(days=STALE_DRAFT_DAYS)).isoformat()
    moved = 0
    with connect() as con:
        rows = con.execute(
            "SELECT l.id "
            "FROM leads l "
            "WHERE l.status = 'Drafted' "
            "  AND l.scheduled_send_at IS NULL "
            "  AND COALESCE(l.reviewed_at, '') = '' "
            "  AND COALESCE(l.jaydip_note, '') = '' "
            "  AND ( "
            "    SELECT COALESCE(MAX(e.at), l.first_seen_at) "
            "    FROM events e "
            "    WHERE e.lead_id = l.id AND e.kind = 'draft' "
            "  ) < ? ",
            (cutoff,),
        ).fetchall()
        for r in rows:
            try:
                _archive_lead(con, r["id"], reason=f"auto_stale_draft_{STALE_DRAFT_DAYS}d")
                moved += 1
            except HTTPException:
                continue
        if moved:
            _log_event(con, "stale_drafts_sweep",
                       meta={"archived": moved, "cutoff_days": STALE_DRAFT_DAYS})
        con.commit()
    _stale_sweep_state["last_run_date"] = today
    return moved


# ---------- autopilot tick (called by main.py scheduler) ----------

_autopilot_state = {"last_fired_date": None}


def _autopilot_tick() -> None:
    """Checks safety_state.autopilot_*. If enabled and at-or-past the target
    hour, fires one batch for the day. Safe to call every minute."""
    with connect() as con:
        s = con.execute(
            "SELECT autopilot_enabled, autopilot_hour, autopilot_minute, "
            "autopilot_count, autopilot_tz "
            "FROM safety_state WHERE id=1"
        ).fetchone()
    if not s or not s["autopilot_enabled"]:
        return

    # Evaluate hour/date in the configured TZ so the daily trigger fires at
    # the user's local time regardless of where the server actually runs.
    tz_name = (s["autopilot_tz"] or "").strip()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            now = dt.datetime.now(ZoneInfo(tz_name))
        except Exception:
            now = dt.datetime.now()  # bad TZ string → fall back silently
    else:
        now = dt.datetime.now()
    today = now.date().isoformat()
    if _autopilot_state["last_fired_date"] == today:
        return
    # Compare as (hour, minute) so a 4:30 PM target doesn't fire at 4:00.
    target_min = int(s["autopilot_hour"]) * 60 + int(s["autopilot_minute"] or 0)
    now_min = now.hour * 60 + now.minute
    if now_min < target_min:
        return
    if _batch_state["running"]:
        return
    if gmail.get_credentials() is None:
        return

    try:
        with connect() as con:
            cap = _effective_daily_cap(con)
        # If the user asked for a smaller drip (autopilot_count set),
        # honour it; else fire the full effective cap.
        limit = int(s["autopilot_count"]) if s["autopilot_count"] else cap
        count = min(cap, limit)
        resp = send_batch(BatchSendIn(count=count, source="autopilot"))
        _autopilot_state["last_fired_date"] = today
        with connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO autopilot_runs "
                "(fired_at, fired_date, total_queued, status) VALUES (?, ?, ?, ?)",
                (
                    dt.datetime.now().isoformat(timespec="seconds"),
                    today,
                    int(resp.get("total", 0)),
                    "started",
                ),
            )
            con.commit()
    except HTTPException as e:
        status_map = {
            429: "skipped_quota",
            423: "skipped_quiet_or_paused",
            400: "skipped_no_drafts",
        }
        status = status_map.get(e.status_code, "skipped_other")
        with connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO autopilot_runs "
                "(fired_at, fired_date, total_queued, status) VALUES (?, ?, ?, ?)",
                (
                    dt.datetime.now().isoformat(timespec="seconds"),
                    today, 0, status,
                ),
            )
            _log_event(con, "autopilot_skip",
                       meta={"status": e.status_code, "detail": str(e.detail)[:200]})
            con.commit()
        _autopilot_state["last_fired_date"] = today


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


def _match_reply_to_lead(con, in_reply_to: str, references: str,
                         from_email: str = "", subject: str = "") -> Optional[int]:
    """Find the lead that this inbound message is a reply to.

    Tiered match:
      1) Exact match on sent_message_id via In-Reply-To / References —
         works when Gmail preserves our Message-ID (rare).
      2) Fallback: from_email matches a Sent lead's recipient AND the
         inbound subject is "Re: <lead.gen_subject>" (case-insensitive,
         whitespace-tolerant). Handles the common case where Gmail
         rewrote the outbound Message-ID so threading headers don't
         match our stored id."""
    candidates: list[str] = []
    if in_reply_to:
        candidates.append(in_reply_to.strip("<>").strip())
    for ref in re.split(r"\s+", references or ""):
        ref = ref.strip().strip("<>")
        if ref:
            candidates.append(ref)
    if candidates:
        placeholders = ",".join(["?"] * len(candidates))
        row = con.execute(
            f"SELECT id FROM leads WHERE sent_message_id IN ({placeholders}) LIMIT 1",
            candidates,
        ).fetchone()
        if row:
            return row["id"]

    # Fallback — match by sender + subject.
    mail = (from_email or "").strip().lower()
    subj = (subject or "").strip()
    if not mail or not subj:
        return None
    # Strip common "Re: " / "Fwd:" prefixes, collapse whitespace.
    cleaned = re.sub(r"^\s*(re|fwd?|fw)\s*:\s*", "", subj, flags=re.IGNORECASE)
    cleaned_norm = re.sub(r"\s+", " ", cleaned).strip().lower()
    if not cleaned_norm:
        return None
    row = con.execute(
        "SELECT id, gen_subject FROM leads "
        "WHERE status IN ('Sent', 'Replied') "
        "  AND LOWER(TRIM(email)) = ? "
        "  AND gen_subject IS NOT NULL "
        "ORDER BY sent_at DESC",
        (mail,),
    ).fetchall()
    for r in row:
        gs = re.sub(r"\s+", " ", (r["gen_subject"] or "")).strip().lower()
        if gs and (gs == cleaned_norm or cleaned_norm.startswith(gs) or gs.startswith(cleaned_norm)):
            return r["id"]
    # Last resort: if the sender matches exactly one Sent lead, use that.
    if len(row) == 1:
        return row[0]["id"]
    return None


def _auto_draft_for_reply(reply_id: int, lead_id: int) -> None:
    """Background job: call Bridge to draft a response to this newly-
    received reply, store on the replies row. Runs best-effort; on any
    failure (Bridge offline, parse error) we skip silently — the user
    can still click 'Draft with Claude' in the drawer."""
    try:
        with connect() as con:
            lead = con.execute(
                "SELECT posted_by, gen_subject, gen_body FROM leads WHERE id = ?",
                (lead_id,),
            ).fetchone()
            rep = con.execute(
                "SELECT body, snippet FROM replies WHERE id = ?", (reply_id,),
            ).fetchone()
            # Same style exemplars the on-demand drafter uses, so the
            # background auto-draft also benefits from the learned voice.
            examples = _recent_style_examples(con, limit=5)
        if not (lead and rep):
            return
        reply_text = (rep["body"] or rep["snippet"] or "").strip()
        if not reply_text:
            return
        first = _first_name_from_posted_by(lead["posted_by"] or "")
        draft, _raw = linkedin_claude.generate_reply_draft(
            prospect_first_name=first,
            prospect_reply_text=reply_text,
            original_subject=lead["gen_subject"] or "",
            original_body=lead["gen_body"] or "",
            style_examples=examples,
        )
        if not draft:
            return
        now = dt.datetime.now().isoformat(timespec="seconds")
        with connect() as con:
            con.execute(
                "UPDATE replies SET auto_draft_body = ?, auto_draft_at = ? "
                "WHERE id = ?",
                (draft, now, reply_id),
            )
            _log_event(con, "auto_draft", lead_id=lead_id,
                       meta={"reply_id": reply_id, "chars": len(draft)})
            con.commit()
    except Exception as e:
        print(f"[auto-draft] lead={lead_id} reply={reply_id} failed: {e}")


def _poll_and_store() -> dict:
    """Fetch new inbox messages across ALL active Gmail accounts, match
    against sent leads, update lead status on replies/bounces. Returns counts."""
    with connect() as con:
        accounts = con.execute(
            "SELECT id, imap_uid_seen FROM gmail_accounts "
            "WHERE status = 'active' ORDER BY id ASC"
        ).fetchall()

    all_msgs: list = []
    per_account_new_uid: dict[int, int] = {}
    per_account_since: dict[int, int] = {}
    for a in accounts:
        acc_id = a["id"]
        since = int(a["imap_uid_seen"] or 0)
        per_account_since[acc_id] = since
        try:
            msgs, new_uid = gmail.poll_recent(account_id=acc_id, since_uid=since)
        except Exception:
            continue
        all_msgs.extend(msgs)
        per_account_new_uid[acc_id] = new_uid

    counts = {"fetched": len(all_msgs), "replies": 0, "bounces": 0,
              "auto_replies": 0, "matched": 0}
    # (reply_id, lead_id) tuples for replies inserted during this poll.
    # We queue Bridge drafter threads AFTER the DB commit so the user
    # sees the Replied status immediately; draft lands seconds later.
    new_reply_ids_for_drafting: list[tuple[int, int]] = []
    # Expose each account's uid range — callers can surface per-account cursor
    since_uid = min(per_account_since.values()) if per_account_since else 0
    new_uid = max(per_account_new_uid.values()) if per_account_new_uid else since_uid
    msgs = all_msgs

    with connect() as con:
        for m in msgs:
            lead_id = _match_reply_to_lead(
                con, m.in_reply_to, m.references,
                from_email=m.from_email, subject=m.subject,
            )
            if not lead_id:
                continue
            # Content-level dedup. UNIQUE(gmail_msg_id) catches a re-poll
            # of the same physical mail, but cannot catch the case where
            # the sender's mailer fires two copies of the same email
            # with different Message-IDs (auto-responder retry, list
            # double-trigger, etc). Identical (lead, from, subject, body)
            # is treated as the same reply.
            if con.execute(
                "SELECT 1 FROM replies WHERE lead_id = ? AND from_email = ? "
                "AND subject = ? AND body = ? LIMIT 1",
                (lead_id, m.from_email or "", m.subject or "", m.body or ""),
            ).fetchone():
                continue
            counts["matched"] += 1
            classify_text = (m.body or m.snippet or "") + "\n" + (m.subject or "")
            sentiment = linkedin_claude.classify_sentiment(classify_text) if m.kind == "reply" else None
            intent = linkedin_claude.classify_intent(classify_text) if m.kind == "reply" else None
            cur = con.execute(
                "INSERT OR IGNORE INTO replies "
                "(lead_id, gmail_msg_id, from_email, subject, snippet, body, "
                "received_at, kind, sentiment, intent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (lead_id, m.message_id, m.from_email, m.subject, m.snippet,
                 m.body, m.received_at, m.kind, sentiment, intent),
            )
            # rowcount=1 only when a brand new row was inserted (not a
            # dupe). We use this to queue auto-drafts so re-polled old
            # replies don't re-generate drafts each time.
            newly_inserted_id = cur.lastrowid if cur.rowcount > 0 else None
            if m.kind == "reply":
                counts["replies"] += 1
                con.execute(
                    "UPDATE leads SET status = 'Replied', replied_at = ?, "
                    "needs_attention = 1 WHERE id = ? AND status != 'Replied'",
                    (m.received_at, lead_id),
                )
                # Soft opt-out: if the reply smells like "not interested /
                # stop / remove me", auto-add the sender email to the
                # blocklist so future batches (and follow-ups) skip them
                # without needing manual intervention. This honours both
                # the courtesy line we ask Claude to include in drafts
                # and any unprompted STOP replies.
                if sentiment == "not_interested" and m.from_email:
                    sender = m.from_email.strip().lower()
                    existing = con.execute(
                        "SELECT 1 FROM blocklist WHERE kind='email' AND value=?",
                        (sender,),
                    ).fetchone()
                    if not existing:
                        con.execute(
                            "INSERT INTO blocklist (kind, value, reason, created_at) "
                            "VALUES ('email', ?, 'auto:reply opt-out', ?)",
                            (sender, dt.datetime.now().isoformat(timespec="seconds")),
                        )
                        _log_event(
                            con, "auto_blocklist",
                            lead_id=lead_id,
                            meta={"email": sender, "trigger": "not_interested_reply"},
                        )
                if newly_inserted_id is not None:
                    new_reply_ids_for_drafting.append(
                        (newly_inserted_id, lead_id)
                    )
                # OOO auto-nudge: stamp a follow-up ~7 days out at 9am
                # local so the prospect hears from us again when they're
                # likely back at their desk. Only stamp if not already
                # scheduled and no nudge has been sent yet.
                if sentiment == "ooo":
                    existing = con.execute(
                        "SELECT ooo_nudge_at, ooo_nudge_sent_at "
                        "FROM leads WHERE id = ?", (lead_id,),
                    ).fetchone()
                    if existing and not existing["ooo_nudge_at"] and not existing["ooo_nudge_sent_at"]:
                        nudge_when = dt.datetime.now().astimezone() + dt.timedelta(days=7)
                        nudge_when = nudge_when.replace(
                            hour=9, minute=0, second=0, microsecond=0,
                        )
                        con.execute(
                            "UPDATE leads SET ooo_nudge_at = ? WHERE id = ?",
                            (nudge_when.isoformat(timespec="seconds"), lead_id),
                        )
                        _log_event(con, "ooo_nudge_scheduled", lead_id=lead_id,
                                   meta={"send_at": nudge_when.isoformat()})
            elif m.kind == "bounce":
                counts["bounces"] += 1
                # Replied wins over Bounced. If a real reply already came
                # through (e.g. recipient replied from a different alias and
                # an out-of-date NDR for the original address shows up
                # later), don't downgrade the lead — Replied is more
                # informative and downstream filters (/followups,
                # replied_at timestamp) depend on it.
                con.execute(
                    "UPDATE leads SET status = 'Bounced', bounced_at = ? "
                    "WHERE id = ? AND status NOT IN ('Replied', 'Bounced')",
                    (m.received_at, lead_id),
                )
                # Attribute the bounce back to the sending account so the
                # auto-pause rail can trip if one inbox is landing
                # disproportionate bounces.
                acct_row = con.execute(
                    "SELECT sent_via_account_id, email FROM leads WHERE id = ?",
                    (lead_id,),
                ).fetchone()
                if acct_row and acct_row["sent_via_account_id"]:
                    try:
                        gmail.record_bounce(int(acct_row["sent_via_account_id"]))
                    except Exception:
                        pass
                # Auto-domain-block: if this recipient's domain has now
                # bounced 2+ times in the last 30 days, add the domain to
                # the blocklist so future sends / ingests skip it. Cheap
                # guard — a typo or one-off mailbox issue stays single-
                # bounce and won't trip this. Recipient email is taken
                # from the lead row (not the inbound sender, which could
                # be the MAILER-DAEMON address).
                recipient = (acct_row["email"] or "").strip().lower() if acct_row else ""
                domain = recipient.split("@", 1)[1] if "@" in recipient else ""
                if domain:
                    bounces_for_domain = con.execute(
                        "SELECT COUNT(*) FROM leads "
                        "WHERE LOWER(SUBSTR(email, INSTR(email, '@') + 1)) = ? "
                        "  AND bounced_at IS NOT NULL "
                        "  AND DATE(bounced_at) >= DATE('now', '-30 day')",
                        (domain,),
                    ).fetchone()[0]
                    if bounces_for_domain >= 2:
                        existing = con.execute(
                            "SELECT 1 FROM blocklist WHERE kind='domain' AND value=?",
                            (domain,),
                        ).fetchone()
                        if not existing:
                            con.execute(
                                "INSERT INTO blocklist (kind, value, reason, created_at) "
                                "VALUES ('domain', ?, 'auto:repeat-bounces', ?)",
                                (domain, dt.datetime.now().isoformat(timespec="seconds")),
                            )
                            _log_event(
                                con, "auto_blocklist_domain",
                                lead_id=lead_id,
                                meta={"domain": domain,
                                      "bounces_30d": bounces_for_domain,
                                      "trigger": "repeat_bounces"},
                            )
            else:
                counts["auto_replies"] += 1

            _log_event(con, f"inbox_{m.kind}", lead_id=lead_id,
                       meta={"msg_id": m.message_id, "from": m.from_email})

        for acc_id, uid in per_account_new_uid.items():
            if uid > per_account_since.get(acc_id, 0):
                con.execute(
                    "UPDATE gmail_accounts SET imap_uid_seen = ? WHERE id = ?",
                    (uid, acc_id),
                )
        con.commit()

    # Fire auto-draft threads after commit so the Bridge doesn't hold
    # the DB transaction open for seconds. Uses default daemon=True so
    # these don't block app shutdown.
    for reply_id, lead_id in new_reply_ids_for_drafting:
        import threading
        t = threading.Thread(
            target=_auto_draft_for_reply,
            args=(reply_id, lead_id),
            daemon=True,
        )
        t.start()

    return {**counts, "since_uid": since_uid, "new_uid": new_uid,
            "drafting": len(new_reply_ids_for_drafting)}


@router.post("/replies/poll")
def poll_replies():
    try:
        return _poll_and_store()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Poll failed: {e}")
