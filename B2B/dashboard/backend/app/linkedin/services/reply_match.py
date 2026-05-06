"""
Reply -> lead attribution. Extracted from linkedin_api.py so the matcher
can be tested in isolation (see tests/test_match_reply.py) and replaced
with smarter logic later (e.g. embedding similarity for forwards / list
mail) without touching the polling loop.

Public surface is intentionally tiny:
    match_reply_to_lead(con, in_reply_to, references, from_email, subject)
    first_name_from_posted_by(raw)

linkedin_api re-exports both as `_match_reply_to_lead` /
`_first_name_from_posted_by` so existing call sites (and the rest of the
file) keep working unchanged.
"""
from __future__ import annotations

import re
from typing import Optional


def match_reply_to_lead(con, in_reply_to: str, references: str,
                        from_email: str = "", subject: str = "") -> Optional[int]:
    """Find the lead that this inbound message is a reply to.

    Tiered match:
      1) Exact match on sent_message_id via In-Reply-To / References —
         works when Gmail preserves our Message-ID (rare).
      2) Fallback: from_email matches a Sent lead's recipient AND the
         inbound subject is "Re: <lead.gen_subject>" (case-insensitive,
         whitespace-tolerant). Handles the common case where Gmail
         rewrote the outbound Message-ID so threading headers don't
         match our stored id.
    """
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
            f"SELECT id FROM ln_leads WHERE sent_message_id IN ({placeholders}) LIMIT 1",
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
    rows = con.execute(
        "SELECT id, gen_subject FROM ln_leads "
        "WHERE status IN ('Sent', 'Replied') "
        "  AND LOWER(TRIM(email)) = ? "
        "  AND gen_subject IS NOT NULL "
        "ORDER BY sent_at DESC",
        (mail,),
    ).fetchall()
    for r in rows:
        gs = re.sub(r"\s+", " ", (r["gen_subject"] or "")).strip().lower()
        if gs and (gs == cleaned_norm or cleaned_norm.startswith(gs) or gs.startswith(cleaned_norm)):
            return r["id"]
    # Last resort: if the sender matches exactly one Sent lead, use that.
    if len(rows) == 1:
        return rows[0]["id"]
    return None


def first_name_from_posted_by(raw: str) -> str:
    """Extract a usable first name from a 'posted_by' string. Empty if
    nothing parseable — caller usually falls back to a generic 'Hi there'."""
    s = (raw or "").strip().split()
    return s[0].capitalize() if s else ""
