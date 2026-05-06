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

from app.linkedin.db import connect
from app.linkedin.schemas import BlocklistBulkIn, BlocklistIn, CVMeta, FollowupRunIn


import csv
import io

from fastapi.responses import PlainTextResponse

CV_CLUSTERS = ("python", "ml", "ai_llm", "fullstack", "scraping", "n8n", "default")
# CVs are served by the FastAPI static mount (see app.main: /static).
# `app/static/cvs/` lives one level above `app/linkedin/extras.py`.
CV_STORAGE_DIR = Path(__file__).resolve().parents[1] / "static" / "cvs"

# How long a lead can sit in Sending/Queued before we call it orphaned.
ORPHAN_AFTER_MINUTES = 10

# Follow-up cadence (days after the last outgoing touch).
FOLLOWUP_DAYS = (3, 7)


# ---------------------------------------------------------------- helpers


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _log(con, kind: str, lead_id: Optional[int] = None, meta: Optional[dict] = None):
    con.execute(
        "INSERT INTO ln_events (at, kind, lead_id, meta_json) VALUES (?, ?, ?, ?)",
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
            "UPDATE ln_leads SET status = 'Drafted' "
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






def _archive_lead_inline(con, lead_id: int, reason: str) -> None:
    """Copy of _archive_lead from linkedin_api to avoid circular import."""
    row = con.execute("SELECT * FROM ln_leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        return
    payload = {k: row[k] for k in row.keys()}
    con.execute(
        "INSERT OR REPLACE INTO ln_recyclebin "
        "(original_id, post_url, payload_json, reason, moved_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (lead_id, row["post_url"], json.dumps(payload), reason, _now_iso()),
    )
    con.execute("DELETE FROM ln_leads WHERE id = ?", (lead_id,))






# Legacy endpoint kept for old clients / existing docs. Behaves like clear
# (dedup-preserving) — the safer default.


# -------------------------------------------------- autopilot status




# -------------------------------------------------- blocklist


_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-.]*\.[a-z]{2,}$")
_EMAIL_RE  = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def _archive_matching_leads(con, kind: str, value: str, reason: str) -> int:
    """Move leads already in the DB that match a newly-added blocklist
    entry into ln_recyclebin. Prevents them from appearing in Drafted queues
    after the block rule is added. Returns count archived.

    Only touches leads that haven't been Sent yet — already-sent leads are
    left as historical record."""
    value = value.lower()
    if kind == "email":
        rows = con.execute(
            "SELECT id FROM ln_leads "
            "WHERE LOWER(TRIM(email)) = ? AND status != 'Sent'",
            (value,),
        ).fetchall()
    elif kind == "domain":
        # Match emails ending in @<domain> OR @sub.<domain>.
        rows = con.execute(
            "SELECT id FROM ln_leads WHERE status != 'Sent' AND ("
            "  LOWER(email) LIKE ? OR LOWER(email) LIKE ?"
            ")",
            (f"%@{value}", f"%.{value}"),
        ).fetchall()
    elif kind == "company":
        rows = con.execute(
            "SELECT id FROM ln_leads "
            "WHERE status != 'Sent' AND LOWER(COALESCE(company, '')) LIKE ?",
            (f"%{value}%",),
        ).fetchall()
    else:
        return 0

    archived = 0
    for r in rows:
        lead_id = r["id"]
        row = con.execute("SELECT * FROM ln_leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            continue
        con.execute(
            "INSERT OR REPLACE INTO ln_recyclebin "
            "(original_id, post_url, payload_json, reason, moved_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                lead_id, row["post_url"],
                json.dumps({k: row[k] for k in row.keys()}),
                f"blocklist:{kind}:{value} ({reason})",
                _now_iso(),
            ),
        )
        con.execute("DELETE FROM ln_leads WHERE id = ?", (lead_id,))
        archived += 1
    return archived














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
            "SELECT kind, value, reason FROM ln_blocklist"
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
            "SELECT stored_path, filename FROM ln_cvs WHERE cluster = ?", (key,)
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
            "SELECT 1 FROM ln_cvs WHERE cluster = ?", (c,)
        ).fetchone()
    return None if row else c


# -------------------------------------------------- follow-ups




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










# -------------------------------------------------- per-lead event timeline




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


# Re-export surface for the per-domain routers.
__all__ = [
    'APIRouter',
    'BaseModel',
    'BlocklistBulkIn',
    'BlocklistIn',
    'CVMeta',
    'CV_CLUSTERS',
    'CV_STORAGE_DIR',
    'FOLLOWUP_DAYS',
    'FOLLOWUP_TEMPLATE_1',
    'FOLLOWUP_TEMPLATE_2',
    'Field',
    'File',
    'FollowupRunIn',
    'Form',
    'HTTPException',
    'ORPHAN_AFTER_MINUTES',
    'Optional',
    'Path',
    'PlainTextResponse',
    'UploadFile',
    '_DOMAIN_RE',
    '_EMAIL_RE',
    '_archive_lead_inline',
    '_archive_matching_leads',
    '_build_followup_body',
    '_csv_response',
    '_first_name',
    '_log',
    '_now_iso',
    'annotations',
    'connect',
    'csv',
    'cv_required_but_missing',
    'dt',
    'io',
    'is_blocked',
    'json',
    'pick_cv_path',
    're',
    'reset_orphans',
    'shutil',
]
