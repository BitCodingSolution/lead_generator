"""Heuristic lead scoring — 0 to 100. No ML, no external calls — runs on
the lead row in memory in microseconds. Re-run whenever the lead's
email/draft/company/role fields change.

Design choice: explicit points per signal so the UI can surface WHY a
lead scored what it scored. This beats a black-box model for a small-
volume B2B funnel where the user wants to trust and override the signal.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional


_TECH_KEYWORDS = re.compile(
    r"\b(python|django|fastapi|flask|ai|ml|machine\s*learning|"
    r"llm|gpt|claude|rag|agent|agents|pytorch|tensorflow|data\s*engineer|"
    r"data\s*scientist|nlp|langchain|langgraph|anthropic|openai|"
    r"azure|aws|databricks|pyspark|airflow|snowflake|backend|devops|"
    r"mlops|kubernetes|docker)\b",
    re.IGNORECASE,
)

_GENERIC_INBOX_RE = re.compile(
    r"^(hr|careers?|jobs?|hiring|recruiting|recruiter|talent|info|"
    r"contact|hello|admin|support|team|no[-]?reply|people[-]?ops|"
    r"(whole[- ]page fallback))$",
    re.IGNORECASE,
)


def compute_score(lead: dict) -> tuple[int, list[str]]:
    """Return (score 0-100, list of human-readable reasons).

    `lead` is a dict-like with at least: email, role, tech_stack,
    gen_subject, gen_body, company, phone, posted_by, post_url,
    first_seen_at."""
    score = 0
    reasons: list[str] = []

    email = (lead.get("email") or "").strip()
    if email and "@" in email:
        score += 20
        reasons.append("+20 has email")
    else:
        # No email = can't cold-outreach via this pipeline at all.
        # Leave score at 0 and return early — nothing else matters.
        return 0, ["no email — cannot outreach"]

    role_blob = " ".join([
        lead.get("role") or "",
        lead.get("tech_stack") or "",
        lead.get("gen_subject") or "",
    ])
    if _TECH_KEYWORDS.search(role_blob):
        score += 25
        reasons.append("+25 role mentions target tech")

    if (lead.get("company") or "").strip():
        score += 10
        reasons.append("+10 has company")

    if (lead.get("phone") or "").strip():
        score += 5
        reasons.append("+5 has phone")

    gen_subject = (lead.get("gen_subject") or "").strip()
    gen_body = (lead.get("gen_body") or "").strip()
    if gen_subject and gen_body:
        score += 15
        reasons.append("+15 draft ready")

    posted_by = (lead.get("posted_by") or "").strip()
    if posted_by and not _GENERIC_INBOX_RE.match(posted_by):
        score += 10
        reasons.append("+10 named person (not HR inbox)")

    # Source quality: LinkedIn native vs whole-page fallback. post_url
    # containing linkedin.fallback is what the extension generates for
    # non-post scans.
    post_url = (lead.get("post_url") or "").lower()
    if "linkedin.fallback" not in post_url and "linkedin.com" in post_url:
        score += 10
        reasons.append("+10 LinkedIn-native source")

    # Freshness
    first_seen = lead.get("first_seen_at") or ""
    if first_seen:
        try:
            seen = dt.datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
            age = dt.datetime.now(seen.tzinfo or dt.timezone.utc) - seen
            if age.days <= 3:
                score += 5
                reasons.append("+5 fresh (<= 3 days old)")
        except Exception:
            pass

    return min(score, 100), reasons


def priority_band(score: Optional[int]) -> str:
    """UI colour bucket. Returns 'high' | 'medium' | 'low' | 'unscored'."""
    if score is None:
        return "unscored"
    if score >= 75:
        return "high"
    if score >= 50:
        return "medium"
    return "low"
