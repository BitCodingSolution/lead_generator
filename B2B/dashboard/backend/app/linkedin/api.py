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
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from app.linkedin import db as linkedin_db
from app.linkedin.db import connect, init
from app.linkedin.services.claude import (
    BridgeParseError,
    BridgeUnreachable,
    bridge_is_up,
    draft_variety_key,
    generate_draft as _claude_generate,
)
from app.linkedin.services import claude as linkedin_claude
from app.linkedin.services import scoring as linkedin_scoring
from app.linkedin.services import gmail as gmail
from app.linkedin import extras as extras
from app.linkedin.schemas import (
    AccountWarning,
    ArchiveRequest,
    AutoPausedAccount,
    AutopilotTodayRun,
    BatchSendIn,
    BulkHandleBody,
    BulkLeadIdsBody,
    BulkSnoozeBody,
    DraftBatchIn,
    DraftReplyBody,
    ExtensionKeyIn,
    GmailCapIn,
    GmailConnectIn,
    GmailWarmupIn,
    IngestBatch,
    IngestPost,
    LeadPatch,
    LinkedInLead,
    MarkHandledBody,
    OverviewResponse,
    RuntimeSettingUpdate,
    SafetyPatch,
    SafetyState,
    ScheduleBody,
    SendReplyBody,
    SnoozeBody,
    WarmupCurveIn,
)

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


# Ensure schema exists on import (safe/idempotent).
init()


# ---------- models ----------












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












# ---- Runtime settings (kv_settings table) ---------------------------------
# Surface the env-only flags that previously required a backend restart.
# Each entry is {key, label, type, env_key, default} so the frontend can
# render the right input + tooltip without hard-coding the schema.

_RUNTIME_SETTINGS = [
    {
        "key": "linkedin.digest.enabled",
        "label": "Daily outreach digest email",
        "type": "bool",
        "env_key": "LINKEDIN_DIGEST_ENABLED",
        "default": False,
        "help": "9am summary of yesterday's sent/replies/bounces. Off by default — landed in your own inbox as noise.",
    },
    {
        "key": "linkedin.draft.plan",
        "label": "Drafter: planning step",
        "type": "bool",
        "env_key": "LINKEDIN_DRAFT_PLAN",
        "default": True,
        "help": "Extra Claude call that picks angle + hook before writing. ~2x token cost. Worth it for quality.",
    },
    {
        "key": "linkedin.draft.critique",
        "label": "Drafter: critique step",
        "type": "bool",
        "env_key": "LINKEDIN_DRAFT_CRITIQUE",
        "default": True,
        "help": "Extra Claude call that critiques + rewrites the draft. ~2x token cost.",
    },
    {
        "key": "linkedin.draft.stats_hints",
        "label": "Drafter: stats-aware hints",
        "type": "bool",
        "env_key": "LINKEDIN_DRAFT_STATS_HINTS",
        "default": True,
        "help": "Feeds reply-rate/bounce stats into the prompt so Claude steers away from low-performing patterns.",
    },
    {
        "key": "linkedin.draft.enrichment",
        "label": "Drafter: company enrichment",
        "type": "bool",
        "env_key": "LINKEDIN_DRAFT_ENRICHMENT",
        "default": True,
        "help": "Best-effort homepage fetch + meta-description per company. Adds 0-4s on first draft, cached after.",
    },
]










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










# ---------- ingest (extension → dashboard) ----------






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




# ---------- account-warning pause ----------






# ---------- lead detail, edit, archive, restore ----------










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
















# ---------- Claude draft generation ----------




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






# ---------- Gmail connect / test / disconnect ----------


































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
















# ---------- Send flow ----------














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

    Off by default. Toggle from the Settings page (or set
    LINKEDIN_DIGEST_ENABLED=1 — env still works as a one-off override).
    Disabled per user request: the digest landed in their own outreach
    inbox and was visual noise.
    """
    if not linkedin_db.get_setting_bool(
        "linkedin.digest.enabled", env_key="LINKEDIN_DIGEST_ENABLED",
        default=False,
    ):
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




# _match_reply_to_lead and _first_name_from_posted_by were moved into
# linkedin_reply_match.py so they can be unit-tested without dragging the
# whole FastAPI import graph along. The aliases below preserve the
# private-name call sites elsewhere in this module.
from app.linkedin.services.reply_match import (  # noqa: E402
    match_reply_to_lead as _match_reply_to_lead,
    first_name_from_posted_by as _first_name_from_posted_by,
)


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
        t = threading.Thread(
            target=_auto_draft_for_reply,
            args=(reply_id, lead_id),
            daemon=True,
        )
        t.start()

    return {**counts, "since_uid": since_uid, "new_uid": new_uid,
            "drafting": len(new_reply_ids_for_drafting)}


# Re-export surface for the per-domain routers.
__all__ = [
    'APIRouter',
    'AccountWarning',
    'ArchiveRequest',
    'AutoPausedAccount',
    'AutopilotTodayRun',
    'BATCH_JITTER_MAX_S',
    'BATCH_JITTER_MIN_S',
    'BaseModel',
    'BatchSendIn',
    'BridgeParseError',
    'BridgeUnreachable',
    'BulkHandleBody',
    'BulkLeadIdsBody',
    'BulkSnoozeBody',
    'DAILY_CAP',
    'DRAFT_WORKERS',
    'DraftBatchIn',
    'DraftReplyBody',
    'ExtensionKeyIn',
    'Field',
    'GmailCapIn',
    'GmailConnectIn',
    'GmailWarmupIn',
    'HTTPException',
    'Header',
    'IngestBatch',
    'IngestPost',
    'LeadPatch',
    'LinkedInLead',
    'MarkHandledBody',
    'Optional',
    'OverviewResponse',
    'Query',
    'REJECTION_NOTE_RE',
    'Request',
    'Response',
    'RuntimeSettingUpdate',
    'STALE_DRAFT_DAYS',
    'SafetyPatch',
    'SafetyState',
    'ScheduleBody',
    'SendReplyBody',
    'SnoozeBody',
    'StreamingResponse',
    'TRACKING_BASE_URL',
    'WARNING_PAUSE_DAYS',
    'WARNING_PHRASES_RE',
    'WarmupCurveIn',
    '_EXT_DIR',
    '_OOO_NUDGE_BODY',
    '_Path',
    '_RUNTIME_SETTINGS',
    '_TRACKING_PIXEL_BYTES',
    '_archive_lead',
    '_auto_draft_for_reply',
    '_autopilot_state',
    '_autopilot_tick',
    '_batch_context',
    '_batch_context_lock',
    '_batch_lock',
    '_batch_state',
    '_batch_stop_event',
    '_batch_thread',
    '_batch_worker',
    '_check_safety_before_send',
    '_claude_generate',
    '_digest_already_sent',
    '_digest_state',
    '_digest_tick',
    '_drafts_lock',
    '_drafts_state',
    '_drafts_worker',
    '_effective_daily_cap',
    '_ensure_open_token',
    '_first_name_from_posted_by',
    '_followups_state',
    '_followups_tick',
    '_generate_one',
    '_lead_temperature',
    '_log_event',
    '_mark_digest_sent',
    '_match_reply_to_lead',
    '_not_yet',
    '_pick_ready_leads',
    '_poll_and_store',
    '_recent_style_examples',
    '_record_failure',
    '_record_send',
    '_require_ext_key',
    '_rescore',
    '_roll_daily_counter',
    '_scheduler_tick',
    '_send_ooo_nudge',
    '_stale_drafts_sweep',
    '_stale_sweep_state',
    '_today',
    '_tracking_is_public',
    '_tracking_pixel_url',
    '_upsert_lead',
    'annotations',
    'bridge_is_up',
    'connect',
    'draft_variety_key',
    'dt',
    'extras',
    'field_validator',
    'gmail',
    'init',
    'io',
    'json',
    'linkedin_claude',
    'linkedin_db',
    'linkedin_scoring',
    'os',
    'random',
    're',
    'secrets',
    'threading',
    'zipfile',
]
