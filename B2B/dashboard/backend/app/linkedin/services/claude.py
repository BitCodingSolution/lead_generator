"""
LinkedIn draft generator — calls the existing B2B Claude Bridge at
http://127.0.0.1:8766/generate-reply and returns a structured result:

    {
      subject, body, email_mode,
      should_skip, skip_reason, skip_source,
      cv_cluster
    }

Prompt is ported from the legacy extension (v3.18 schema) but tightened for
JSON-only output. No Anthropic key lives in this process — the Bridge
forwards to Claude on our behalf.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import requests

BRIDGE_URL = "http://127.0.0.1:8766/generate-reply"
BRIDGE_TIMEOUT_S = 180

# ---- Feature switches ------------------------------------------------------
# These gate the extra Claude calls we layer on top of the main drafter. Each
# extra call roughly doubles per-lead token spend, so the user can flip any
# off from the Settings page. Reads go through linkedin_db.get_setting_bool
# which honours DB > env > default — env still works as a one-off override.
# Default ON: the whole point of the LinkedIn flow is quality > throughput.

def _flag(key: str, env_key: str) -> bool:
    # Lazy-import linkedin_db to avoid a circular import (linkedin_db is
    # tiny but linkedin_claude is imported very early during app startup).
    import linkedin_db  # noqa: WPS433
    return linkedin_db.get_setting_bool(key, env_key=env_key, default=True)


def is_plan_step_enabled() -> bool:
    return _flag("linkedin.draft.plan", "LINKEDIN_DRAFT_PLAN")


def is_critique_enabled() -> bool:
    return _flag("linkedin.draft.critique", "LINKEDIN_DRAFT_CRITIQUE")


def is_stats_hints_enabled() -> bool:
    return _flag("linkedin.draft.stats_hints", "LINKEDIN_DRAFT_STATS_HINTS")


def is_enrichment_enabled() -> bool:
    return _flag("linkedin.draft.enrichment", "LINKEDIN_DRAFT_ENRICHMENT")


class BridgeUnreachable(Exception):
    """Claude Bridge (localhost:8766) is offline or unreachable. Raised by
    generate_draft so the caller can refuse cleanly instead of falling back
    to a regex-only decision that might mis-archive a real lead."""


class BridgeParseError(Exception):
    """Bridge answered but the payload wasn't a parseable JSON object.
    Transient — the caller should surface as a retryable 502."""


def bridge_is_up(timeout: float = 1.5) -> bool:
    """Cheap health probe. Returns True only when OUR bridge answers on
    /health with the expected service-name signature. Used by batch-drafter
    preflight so we don't spawn a worker that would immediately refuse
    every lead — and to catch port-squatters (a foreign process answering
    on :8766 must NOT be treated as a healthy bridge)."""
    try:
        r = requests.get("http://127.0.0.1:8766/health", timeout=timeout)
        if r.status_code != 200:
            return False
        body = r.json()
        return str(body.get("service", "")).startswith("LinkedIn Smart Search")
    except (requests.exceptions.RequestException, ValueError):
        return False


# Specialty keyword clusters for CV picking.
# Clusters are scored by "count of matching keywords in the post", highest
# score wins. ML (classical / DL / CV) and AI_LLM (LLM / agents / RAG) are
# kept separate so the right CV lands for each — a Data Scientist post
# shouldn't get the LLM/agents CV and vice versa.
CV_SPECIALTY_PROFILES: dict[str, list[str]] = {
    "python": [
        "python", "django", "drf", "fastapi", "flask",
        "pydantic", "celery", "pytest", "asyncio", "sqlalchemy",
        "python developer", "backend python", "rest api",
    ],
    "ml": [
        "ml", "machine learning", "deep learning", "neural network",
        "tensorflow", "pytorch", "scikit-learn", "sklearn", "keras",
        "xgboost", "lightgbm", "computer vision", "cv model", "yolo",
        "opencv", "image classification", "object detection", "segmentation",
        "mlops", "model training", "model deployment", "feature engineering",
        "recommendation system", "time series", "forecasting",
        "ml engineer", "machine learning engineer", "data scientist",
        "nlp", "named entity", "sentiment analysis",
    ],
    "ai_llm": [
        "ai", "llm", "large language model", "gpt", "openai",
        "anthropic", "claude", "gemini", "mistral",
        "langchain", "langgraph", "llamaindex", "crewai",
        "agent", "agents", "multi-agent", "autonomous agent",
        "rag", "retrieval augmented", "chatbot", "huggingface",
        "transformer", "embedding", "embeddings",
        "vector db", "vector database", "pinecone", "chromadb", "weaviate",
        "fine-tuning", "fine tuning", "prompt engineering",
        "generative ai", "gen ai", "genai", "ai engineer",
    ],
    "fullstack": [
        "full stack", "fullstack", "full-stack", "react", "nextjs", "next.js",
        "node", "nodejs", "express", "typescript", "frontend", "backend",
        "web app", "web application", "mern", "pern",
    ],
    "scraping": [
        "scraping", "scraper", "scrapy", "selenium", "puppeteer", "playwright",
        "beautifulsoup", "lxml", "xpath", "data extraction", "crawler",
        "crawling", "web scraping", "data mining",
    ],
    "n8n": [
        "n8n", "zapier", "make.com", "integromat", "workflow automation",
        "low-code", "no-code", "integration workflow",
    ],
}


@dataclass
class DraftResult:
    subject: str
    body: str
    email_mode: str  # individual | company
    should_skip: bool
    skip_reason: Optional[str]
    skip_source: str  # claude | "" — Bridge-less paths now raise, not fall back
    cv_cluster: Optional[str]
    raw: str
    # Plan JSON returned by the pre-draft reasoning step (None when the
    # plan step was skipped or failed). Batch callers accumulate these so
    # the next lead's plan can vary from previous hook_types.
    plan: Optional[dict] = None


# --- specialty picker (local, deterministic) -------------------------------


def classify_specialty(text: str) -> Optional[str]:
    t = (text or "").lower()
    if not t:
        return None
    best_label: Optional[str] = None
    best_score = 0
    for label, kws in CV_SPECIALTY_PROFILES.items():
        score = sum(1 for k in kws if k in t)
        if score > best_score:
            best_score = score
            best_label = label
    return best_label if best_score > 0 else None


# --- prompt builder --------------------------------------------------------


def _system_prompt() -> str:
    return (
        "You are an extractor + email-mode classifier + cold-email drafter for "
        "a LinkedIn lead tracker used by Jaydip Nakarani.\n\n"
        "Jaydip has TWO outreach identities:\n"
        "- INDIVIDUAL (default — 90%+ of posts): Senior Python / AI-ML Developer, "
        "8+ years, Surat India, remote contracts. Voice: \"I\".\n"
        "- COMPANY (only when collaboration signals present): Co-Founder & CTO of "
        "BitCoding Solutions Pvt Ltd — 30+ engineer Python / AI-ML / automation "
        "team. Voice: \"we/our\".\n\n"
        "Return ONLY a JSON object, no prose, no code fences. Schema:\n"
        "{\n"
        '  "email_mode":    "individual | company",\n'
        '  "email_subject": "subject matching chosen mode",\n'
        '  "email_body":    "body matching chosen mode",\n'
        '  "should_skip":   true | false,\n'
        '  "skip_reason":   "short phrase if skip; empty string otherwise"\n'
        "}\n\n"
        "STEP 0 — SHOULD_SKIP DECISION. Default false. Set true ONLY when ONE of:\n"
        "  A) Not a job post (candidate looking for work, networking, congrats).\n"
        "  B) Onsite / full-time / W2 / green-card / visa-sponsorship only.\n"
        "  C) Internship or <3 yrs experience role.\n"
        "  D) Tech mismatch (e.g. pure .NET, pure Salesforce, pure Java with "
        "no Python/AI adjacency).\n"
        "Negation-aware: \"no visa required\" means the opposite. When in doubt, "
        "DO NOT skip. Still fill email_subject/body even when skipping.\n\n"
        "STEP 1 — EMAIL_MODE. Default individual. Switch to company ONLY on strong "
        "B2B signals: explicit \"partner/agency/vendor/dev shop/outsource\", "
        "multi-dev team ask, CTO/Founder describing full product build, or "
        "explicit \"looking for agency/consultancy\".\n\n"
        "STEP 2 — DRAFT RULES (hard, enforced):\n"
        "- Plain text only. No HTML, no markdown, no bullet/numbered "
        "lists. Plain paragraphs separated by single blank lines.\n"
        "- ASCII only in the body. NEVER use em-dash (—) or en-dash (–) — "
        "use a regular hyphen or a period or a comma. NEVER use smart/"
        "curly quotes — straight \" and ' only. No bullet symbols "
        "(• ◦ ‣). No ellipsis character (use three dots if you must). "
        "No emoji.\n"
        "- Simple, plain English. Words a non-native reader follows on "
        "first pass. No corporate vocabulary (\"leverage\", \"synergy\", "
        "\"streamline\", \"reach out\", \"touch base\", \"circle back\", "
        "\"value-add\", \"empower\"). No AI tone (\"I hope this finds you "
        "well\", \"I am writing to\", \"I wanted to reach out\"). Sound "
        "like a real person sitting in their Gmail typing it themselves.\n"
        "- 60-90 words in the body including the sign-off. The P.S. opt-out "
        "line (see below) does NOT count toward this budget.\n"
        "- Minimal signature: \"Best,\\nJaydip\" (individual) or "
        "\"Best,\\nJaydip Nakarani\\nCo-Founder & CTO, BitCoding Solutions\" "
        "(company).\n"
        "- After the signature, add a short human opt-out line on a "
        "new paragraph. Pick ONE of these wordings (rotate to stay varied): "
        "\"P.S. If this isn't a fit, just reply 'not interested' and I won't "
        "follow up.\" / \"P.S. Happy to stop if this isn't the right time — "
        "just say the word.\" / \"P.S. Reply 'stop' if you'd rather I don't "
        "follow up.\" Keep it casual, lowercase, no brackets. Do NOT write "
        "\"unsubscribe\" — that word reads like a newsletter.\n"
        "- Subject max 65 chars (individual) / 75 chars (company). No \"Re:\", "
        "no emojis, no ALL CAPS, no \"!\".\n"
        "- Copy the post's exact vocabulary for role/tech/project (subject) and "
        "acknowledge the post naturally in the first body line.\n"
        "- Opening: \"Hi <FirstName>,\" if posted_by has a clear first name, "
        "else \"Hi Hiring Manager,\". NEVER \"Hi there,\".\n"
    )


def _user_prompt(*, posted_by: str, company: str, role: str,
                 tech_stack: str, location: str, post_text: str) -> str:
    return (
        "POST TO PROCESS\n"
        "---------------\n"
        f"posted_by: {posted_by or '(unknown)'}\n"
        f"company:   {company or '(unknown)'}\n"
        f"role:      {role or '(unknown)'}\n"
        f"tech:      {tech_stack or '(unknown)'}\n"
        f"location:  {location or '(unknown)'}\n\n"
        f"text:\n{post_text or ''}\n\n"
        "Output the JSON object now."
    )


# --- JSON extraction (lenient) --------------------------------------------


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    # Strip code fences if present.
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = _JSON_BLOCK_RE.search(raw)
    if not m:
        raise ValueError(f"No JSON object found in Claude reply: {raw[:200]!r}")
    return json.loads(m.group(0))


def _strip_dashes(s: str) -> str:
    """Normalise typographic characters that signal AI / auto-typeset
    output. Cold and reply email both run through this on the way out, so
    even if Claude slips a smart quote or em-dash into the body it never
    reaches the recipient.

    Keep additions narrow \u2014 only characters that look obviously
    non-human-typed in a plain Gmail reply box."""
    if not s:
        return ""
    return (s
            # em-dash / en-dash \u2192 ascii hyphen
            .replace("\u2014", "-").replace("\u2013", "-")
            # smart double quotes \u2192 straight
            .replace("\u201c", '"').replace("\u201d", '"')
            # smart single quotes / apostrophes \u2192 straight
            .replace("\u2018", "'").replace("\u2019", "'")
            # ellipsis \u2192 three dots
            .replace("\u2026", "...")
            # bullet glyphs that creep into "professional" lists
            .replace("\u2022", "-").replace("\u25e6", "-").replace("\u2023", "-")
            # non-breaking space \u2192 regular space (Gmail renders both same,
            # but copy-paste from Word docs is a giveaway)
            .replace("\xa0", " ")
            )


# --- Bridge call helpers ---------------------------------------------------


def _bridge_call(system_prompt: str, user_message: str) -> str:
    """Low-level Bridge wrapper. Raises BridgeUnreachable / BridgeParseError
    on failures so every caller can decide how to surface them. Returns the
    raw reply string — parsing is up to the caller."""
    payload = {"system_prompt": system_prompt, "user_message": user_message}
    try:
        r = requests.post(BRIDGE_URL, json=payload, timeout=BRIDGE_TIMEOUT_S)
        r.raise_for_status()
        return (r.json() or {}).get("reply", "")
    except requests.exceptions.RequestException as e:
        raise BridgeUnreachable(str(e)[:200]) from e


# --- Pre-draft plan (Step 2 in the "think then write" flow) ---------------


_PLAN_SYSTEM_PROMPT = (
    "You are a senior cold-email strategist for Jaydip Nakarani. Given one "
    "job/hiring post, produce a TIGHT one-paragraph PLAN for the email that "
    "will be written next. You are NOT writing the email yet — only the "
    "plan that guides its structure.\n\n"
    "Return ONLY JSON with this schema:\n"
    "{\n"
    '  "hook_type":   "question | observation | specific_detail | "\n'
    '                 "compliment | direct_offer",\n'
    '  "tone":        "casual | direct | warm | matter_of_fact",\n'
    '  "length":      "short | medium",                  // short=40-60w, medium=60-90w\n'
    '  "pitch_angle": "<one-line angle tailored to the post>",\n'
    '  "case_study":  "<which specific BitCoding case study to reference or \'none\'>",\n'
    '  "why":         "<one sentence explaining the choice>"\n'
    "}\n\n"
    "Bias toward VARIETY across a batch — avoid defaulting to the same "
    "hook_type repeatedly. The downstream drafter will use this plan to "
    "write a draft that matches it exactly."
)


def _generate_plan(*, posted_by: str, company: str, role: str,
                   tech_stack: str, location: str, post_text: str,
                   prior_plans: Optional[list[dict]] = None) -> Optional[dict]:
    """Call Claude for a drafting plan. Returns the parsed plan dict, or
    None if anything failed — the caller falls back to a planless draft so
    a single Bridge hiccup doesn't block the batch."""
    variety_block = ""
    if prior_plans:
        hooks = ", ".join(p.get("hook_type", "") for p in prior_plans[-4:] if p.get("hook_type"))
        if hooks:
            variety_block = (
                "\n\nPRIOR PLANS IN THIS BATCH (vary from them):\n"
                f"hook_types used: {hooks}\n"
            )
    user_msg = (
        "POST\n----\n"
        f"posted_by: {posted_by or '(unknown)'}\n"
        f"company:   {company or '(unknown)'}\n"
        f"role:      {role or '(unknown)'}\n"
        f"tech:      {tech_stack or '(unknown)'}\n"
        f"location:  {location or '(unknown)'}\n\n"
        f"text:\n{post_text or ''}"
        f"{variety_block}\n\n"
        "Return the plan JSON now."
    )
    try:
        raw = _bridge_call(_PLAN_SYSTEM_PROMPT, user_msg)
        return _parse_json(raw)
    except Exception:
        return None


def _plan_block(plan: dict) -> str:
    if not plan:
        return ""
    return (
        "\nDRAFTING PLAN FOR THIS LEAD (you MUST follow it):\n"
        f"  hook_type:   {plan.get('hook_type', '?')}\n"
        f"  tone:        {plan.get('tone', '?')}\n"
        f"  length:      {plan.get('length', 'medium')}\n"
        f"  pitch_angle: {plan.get('pitch_angle', '?')}\n"
        f"  case_study:  {plan.get('case_study', 'none')}\n"
    )


# --- Prior-draft variety block --------------------------------------------


def _variety_block(prior_drafts: Optional[list[dict]]) -> str:
    """Render a compact "do not repeat these" block the drafter can honour.
    Each prior entry is {subject, first_line, case_study?}. We keep it
    short — just enough for Claude to recognise patterns to avoid, not so
    long that it dominates the prompt."""
    if not prior_drafts:
        return ""
    rows: list[str] = []
    for i, p in enumerate(prior_drafts[-6:], 1):
        subj = (p.get("subject") or "").strip()
        first = (p.get("first_line") or "").strip()
        cs = (p.get("case_study") or "").strip()
        if not subj and not first:
            continue
        rows.append(
            f"  {i}. subject: {subj[:70]!r}\n"
            f"     first:   {first[:90]!r}"
            + (f"\n     case:    {cs[:60]!r}" if cs else "")
        )
    if not rows:
        return ""
    return (
        "\nRECENT BATCH DRAFTS (explicitly VARY from these — different hook, "
        "different opening pattern, different case study):\n"
        + "\n".join(rows) + "\n"
    )


# --- Stats-aware "avoid" hints --------------------------------------------


def _enrichment_block(company: str) -> str:
    """Inline a "what we know about this company" snippet pulled from
    their homepage. Cheap when the cache hits (single SQLite read);
    blocks for ~3s on a cold cache miss but is bypassable via the
    LINKEDIN_DRAFT_ENRICHMENT=0 env switch.

    The drafter is told to use the snippet for hook flavour but NOT to
    quote it verbatim — a recipient seeing their own marketing copy
    pasted back at them is the worst possible 'personalisation'."""
    if not is_enrichment_enabled() or not (company or "").strip():
        return ""
    try:
        from linkedin_enrich import enrich_company
        summary = enrich_company(company)
    except Exception:
        return ""
    if not summary:
        return ""
    return (
        "\n\nCOMPANY CONTEXT (from their homepage — paraphrase, never quote):\n"
        f"  {summary[:400]}\n"
        "Use this only as light flavour for the opening line. The post text "
        "remains the primary signal.\n"
    )


def _stats_hint_block(stats: Optional[dict]) -> str:
    """Convert /outreach-stats output into a short 'avoid' block for the
    drafter. We only warn on buckets with enough volume to be signal (>=5
    sends) AND a reply rate meaningfully below the baseline."""
    if not stats or not is_stats_hints_enabled():
        return ""
    base = float(stats.get("totals", {}).get("reply_rate_pct", 0) or 0)
    if base <= 0:
        return ""
    avoid_openers: list[str] = []
    for row in stats.get("by_subject_first", []):
        if row.get("sent", 0) >= 5 and row.get("reply_rate_pct", 0) < base - 5:
            avoid_openers.append(row["key"])
    length_hint = ""
    for row in stats.get("by_body_length", []):
        if row.get("sent", 0) >= 5 and row.get("reply_rate_pct", 0) < base - 5:
            length_hint = row["key"]
            break
    if not avoid_openers and not length_hint:
        return ""
    bits = []
    if avoid_openers:
        bits.append(
            "Avoid starting the SUBJECT with: "
            + ", ".join(avoid_openers[:5])
            + " (historically low reply rate)."
        )
    if length_hint:
        bits.append(
            f"Avoid {length_hint} bodies — they have underperformed lately."
        )
    return "\nHISTORICAL PERFORMANCE HINTS:\n  " + "\n  ".join(bits) + "\n"


# --- Self-critique pass ----------------------------------------------------


_CRITIQUE_SYSTEM_PROMPT = (
    "You are a strict editor checking a cold-email draft against a fixed "
    "rule list. You are NOT rewriting — you only flag violations. Return "
    "ONLY JSON:\n"
    "{\n"
    '  "ok":         true | false,\n'
    '  "violations": ["short phrase", ...]   // empty when ok is true\n'
    "}\n\n"
    "Fail the draft if ANY of:\n"
    "- Contains em-dash (—) or en-dash (–) anywhere.\n"
    "- Contains smart/curly quotes (“”‘’).\n"
    "- Contains ellipsis character (…).\n"
    "- Contains bullet symbols (•, ◦, ‣).\n"
    "- Contains emoji or non-ASCII letters in the body.\n"
    "- Uses ANY of: leverage, synergy, streamline, reach out, touch base, "
    "circle back, value-add, empower, hope this finds you well, "
    "I am writing to, I wanted to reach out.\n"
    "- Opening is \"Hi there,\" (bad).\n"
    "- Body is outside 40-110 words (excluding the P.S. opt-out line).\n"
    "- Subject uses Re:, emoji, all caps, or exclamation mark.\n"
    "- Missing the P.S. opt-out line.\n"
)


def _critique(subject: str, body: str) -> tuple[bool, list[str]]:
    """Best-effort critique. Returns (ok, violations). On Bridge error we
    pass it through as ok=True so a transient failure doesn't block the
    batch — the drafter's own sanitiser still runs downstream."""
    if not is_critique_enabled():
        return True, []
    user_msg = (
        "DRAFT TO CHECK\n--------------\n"
        f"subject: {subject}\n\n"
        f"body:\n{body}\n\n"
        "Return the critique JSON now."
    )
    try:
        raw = _bridge_call(_CRITIQUE_SYSTEM_PROMPT, user_msg)
        data = _parse_json(raw)
    except Exception:
        return True, []
    ok = bool(data.get("ok", True))
    viols = data.get("violations") or []
    if not isinstance(viols, list):
        viols = [str(viols)]
    return ok, [str(v)[:80] for v in viols][:6]


# --- public entrypoint -----------------------------------------------------


def generate_draft(
    *,
    posted_by: str = "",
    company: str = "",
    role: str = "",
    tech_stack: str = "",
    location: str = "",
    post_text: str = "",
    prior_drafts: Optional[list[dict]] = None,
    prior_plans: Optional[list[dict]] = None,
    stats: Optional[dict] = None,
) -> DraftResult:
    """Generate one cold-email draft.

    The optional kwargs layer in batch-awareness on top of the per-lead
    reasoning:

      - prior_drafts: recent {subject, first_line, case_study} entries from
        the same batch; injected so Claude explicitly varies hooks and
        case studies instead of producing templated output.
      - prior_plans:  their plan JSONs (hook_type etc.) — used by the plan
        step to pick a different angle.
      - stats:        /outreach-stats output; bottom buckets become 'avoid'
        hints in the system prompt.

    All three are best-effort. If the plan or critique step fails we fall
    back gracefully — no feature should block a draft."""

    # Step 1: generate a plan (optional, costs an extra Bridge call).
    plan: Optional[dict] = None
    if is_plan_step_enabled():
        plan = _generate_plan(
            posted_by=posted_by, company=company, role=role,
            tech_stack=tech_stack, location=location, post_text=post_text,
            prior_plans=prior_plans,
        )

    # Step 2: build the main system + user prompts, layering in the plan,
    # the batch-variety block, stats-aware avoid hints, and (best-effort)
    # company enrichment from the homepage.
    system_prompt = _system_prompt() + _stats_hint_block(stats)
    user_message = (
        _user_prompt(
            posted_by=posted_by, company=company, role=role,
            tech_stack=tech_stack, location=location, post_text=post_text,
        )
        + _enrichment_block(company)
        + _plan_block(plan or {})
        + _variety_block(prior_drafts)
    )

    def _one_call(extra_note: str = "") -> tuple[str, dict]:
        msg = user_message + (("\n\n" + extra_note) if extra_note else "")
        raw = _bridge_call(system_prompt, msg)
        try:
            return raw, _parse_json(raw)
        except ValueError as e:
            raise BridgeParseError(str(e)[:200]) from e

    reply, data = _one_call()
    subject = _strip_dashes(str(data.get("email_subject", "")).strip())
    body = _strip_dashes(str(data.get("email_body", "")).strip())
    mode = str(data.get("email_mode", "individual")).strip().lower()
    if mode not in ("individual", "company"):
        mode = "individual"
    should_skip = bool(data.get("should_skip", False))
    skip_reason = str(data.get("skip_reason", "")).strip() or None

    # Step 3: critique + single retry. Skip decisions bypass critique since
    # those drafts aren't going out anyway.
    if not should_skip and subject and body:
        ok, viols = _critique(subject, body)
        if not ok and viols:
            retry_note = (
                "YOUR PREVIOUS ATTEMPT VIOLATED THESE RULES — REWRITE fixing all:\n"
                + "\n".join(f"  - {v}" for v in viols)
            )
            try:
                reply, data = _one_call(retry_note)
                subject = _strip_dashes(str(data.get("email_subject", "")).strip())
                body = _strip_dashes(str(data.get("email_body", "")).strip())
                new_mode = str(data.get("email_mode", mode)).strip().lower()
                if new_mode in ("individual", "company"):
                    mode = new_mode
            except (BridgeUnreachable, BridgeParseError):
                # Retry failed — we still have the first attempt. The
                # _strip_dashes sanitiser above catches the typography
                # issues; corporate-vocab slips through but that's rare
                # enough that shipping the original beats stalling.
                pass

    cv_cluster = classify_specialty(f"{role}\n{tech_stack}\n{post_text}")

    return DraftResult(
        subject=subject,
        body=body,
        email_mode=mode,
        should_skip=should_skip,
        skip_reason=skip_reason if should_skip else None,
        skip_source="claude" if should_skip else "",
        cv_cluster=cv_cluster,
        raw=reply,
        plan=plan,
    )


def draft_variety_key(r: DraftResult) -> dict:
    """Reduce a DraftResult to the compact dict format _variety_block
    expects. Safe to call on skip results too — they just produce an
    empty-ish dict that the block quietly drops."""
    first = (r.body or "").strip().splitlines()
    first_line = first[0] if first else ""
    case = ""
    # Look for the 'case_study' field from the plan; if plan is None, try
    # a shallow pattern sniff on the body ("e.g., ...", "we built ...").
    if r.plan and isinstance(r.plan, dict):
        case = str(r.plan.get("case_study") or "").strip()
    return {"subject": r.subject, "first_line": first_line, "case_study": case}


# --- reply drafter ---------------------------------------------------------


_REPLY_SYSTEM_PROMPT = (
    "You help draft short, professional replies to email responses received "
    "from B2B prospects. The agency (BitCoding Solutions, Surat India) sent "
    "a cold outreach; a prospect replied. Craft a warm, specific reply that "
    "moves the conversation forward.\n\n"
    "WRITE LIKE A REAL HUMAN TYPING IN THEIR GMAIL REPLY BOX. The reader "
    "should feel like Jaydip himself sat down for two minutes and typed "
    "this — not like a tool generated it.\n\n"
    "TWO MODES — pick automatically based on what the prospect sent:\n\n"
    "MODE A: CONVERSATIONAL REPLY (default).\n"
    "  Use when their message is a normal sentence-paragraph email.\n"
    "  Keep it 60-120 words MAX. Short sentences. Real conversation rhythm.\n\n"
    "MODE B: SCREENING-FORM FILL.\n"
    "  Use when their message contains a structured questionnaire — many "
    "field labels followed by colons (e.g. 'Full Name:', 'DOB:', 'Total "
    "Experience:', 'Current CTC:', 'Mandatory Information: ...'). Fill EVERY "
    "field they listed, in the exact order and exact labels they used, one "
    "field per line. Use the PROFILE FACTS block in the user message for "
    "values. If a field isn't in the profile, write 'TBD - will confirm' "
    "instead of inventing. NEVER fabricate Aadhaar/PAN/passport/bank info — "
    "say 'will share over secure channel after acceptance'. Word cap doesn't "
    "apply in this mode — be complete, but values stay one line each, no "
    "extra prose. After the filled form, add a single brief line "
    "(e.g. 'Happy to share anything else needed. Let me know if there's a "
    "deadline.'), then 'Jaydip'.\n\n"
    "Hard rules (BOTH modes):\n"
    "- Simple, plain English. Words a non-native English reader would "
    "follow on first pass. No corporate vocabulary (\"leverage\", "
    "\"synergy\", \"streamline\", \"reach out\", \"touch base\", \"circle "
    "back\", \"value-add\"). No vague hedging (\"I'd love to\", \"that "
    "sounds great\", \"happy to learn more\").\n"
    "- ASCII characters only in the body. NEVER use em-dash (—) or "
    "en-dash (–) — use a regular hyphen or a period or a comma. NEVER use "
    "smart/curly quotes — straight \" and ' only. No bullet symbols "
    "(• ◦ ‣). No ellipsis character — three dots if you must.\n"
    "- No emoji. No ALL CAPS. No exclamation marks. At most one question "
    "mark.\n"
    "- No markdown. No bold. No bullet/numbered lists. Plain paragraphs "
    "separated by single blank lines.\n"
    "- Reference something specific from their reply — don't give a "
    "generic acknowledgement.\n"
    "- If they asked a question, answer it concretely. If they asked for "
    "specifics (rate, availability, samples), give them or commit to a "
    "next step.\n"
    "- End with one low-friction next step (e.g., 'free for a 20-min "
    "call Tue/Wed?', 'happy to share the case study, just reply with "
    "yes').\n"
    "- Minimal signature: just 'Jaydip' on its own line. No company name, "
    "no phone, no tagline, no \"Best regards\" — just the name.\n"
    "- Output ONLY the reply body text. No subject line, no quoted "
    "history, no greeting boilerplate beyond 'Hi <firstname>,' (or "
    "'<firstname>,' is fine).\n"
)


_PROFILE_PATH = Path(__file__).resolve().parent / "jaydip_profile.json"
_VOICE_PATH = Path(__file__).resolve().parent / "jaydip_voice.md"


def _load_voice_doc() -> str:
    """Pinned voice/tone doc that goes into every reply system prompt.
    Cached on first read because the file is static and we don't want
    the I/O cost on every Bridge call."""
    try:
        return _VOICE_PATH.read_text(encoding="utf-8") if _VOICE_PATH.exists() else ""
    except Exception:
        return ""


_VOICE_DOC_CACHE: Optional[str] = None


def _voice_doc() -> str:
    global _VOICE_DOC_CACHE
    if _VOICE_DOC_CACHE is None:
        _VOICE_DOC_CACHE = _load_voice_doc()
    return _VOICE_DOC_CACHE or ""


def _load_profile() -> dict:
    """Load Jaydip's screening profile from the local gitignored JSON.
    Returns {} if the file is missing — drafter falls back to generic
    behaviour without the form-fill superpower."""
    try:
        return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _profile_facts_block(profile: dict) -> str:
    """Render the profile dict as a flat 'Field: value' block Claude can
    quote verbatim into a screening form."""
    if not profile:
        return ""
    sections = [
        ("Personal", profile.get("personal", {})),
        ("Professional", profile.get("professional", {})),
        ("Skill years", profile.get("skill_years", {})),
    ]
    lines: list[str] = []
    for label, section in sections:
        if not isinstance(section, dict):
            continue
        clean = {k: v for k, v in section.items() if k and not k.startswith("_") and v}
        if not clean:
            continue
        lines.append(f"[{label}]")
        for k, v in clean.items():
            # Humanise snake_case keys for Claude to mirror in field labels
            # if the prospect didn't dictate one.
            human_key = k.replace("_", " ")
            lines.append(f"  {human_key}: {v}")
    case_studies = profile.get("case_studies") or []
    if case_studies:
        lines.append("[Case studies (mention if relevant)]")
        for cs in case_studies:
            lines.append(f"  - {cs}")
    return "\n".join(lines)


def _calendar_block(prospect_reply_text: str) -> str:
    """When the inbound reply is asking for a meeting / interview slot
    (positive sentiment + scheduling/interview intent), inject the
    booking link from LINKEDIN_CALENDAR_URL so Claude weaves it into the
    reply naturally instead of asking 'when works for you'.

    Set the env var to a Calendly / Cal.com / Vyte / Doodle link.
    Empty = drafter falls back to its default phrasing."""
    cal_url = os.environ.get("LINKEDIN_CALENDAR_URL", "").strip()
    if not cal_url:
        return ""
    # Only nudge when the prospect is actually open to a call.
    sentiment = classify_sentiment(prospect_reply_text)
    intent = classify_intent(prospect_reply_text)
    asking_to_meet = (
        sentiment == "positive"
        or intent in ("interview_request", "scheduling")
    )
    if not asking_to_meet:
        return ""
    return (
        "\n--- BOOKING LINK ---\n"
        "If the reply commits to a call/interview, end with one short line "
        "like: 'Easiest way to book: " + cal_url + " — pick any open slot.' "
        "Don't paste the URL twice. If the prospect already gave specific "
        "times, just confirm the time instead — only fall back to the link "
        "when they ask for slots.\n"
    )


def _reply_user_prompt(
    *, prospect_first_name: str, prospect_reply_text: str,
    original_subject: str, original_body: str,
    user_hint: str = "",
    style_examples: Optional[list[dict]] = None,
) -> str:
    profile = _load_profile()
    facts_block = _profile_facts_block(profile)

    parts = [
        f"Prospect first name: {prospect_first_name or '(unknown)'}",
        "",
        "Original outreach I sent:",
        f"  Subject: {original_subject or '(no subject)'}",
        "  Body:",
        (original_body or "(not available)").strip(),
        "",
        "Their reply to us:",
        (prospect_reply_text or "(empty)").strip(),
    ]
    if facts_block:
        parts.extend([
            "",
            "--- PROFILE FACTS (use these literal values when filling fields) ---",
            facts_block,
            "--- END PROFILE FACTS ---",
        ])
    # Few-shot style guidance from Jaydip's own past replies. Claude learns
    # voice/length/structure by example without us having to maintain an
    # explicit tone doc.
    if style_examples:
        parts.extend(["", "--- PAST REPLIES FOR STYLE GUIDANCE ONLY ---",
                      "(Mirror the voice, length, and sentence rhythm. DO NOT copy "
                      "content — the new reply must respond to *this* prospect's "
                      "specific message.)"])
        for i, ex in enumerate(style_examples, 1):
            parts.extend([
                "",
                f"Example {i} — they wrote:",
                ex.get("inbound", "").strip(),
                f"Example {i} — I replied:",
                ex.get("outbound", "").strip(),
            ])
        parts.append("--- END OF EXAMPLES ---")
    if user_hint:
        parts.extend([
            "",
            "USER DIRECTION FOR THIS REPLY (highest priority — follow this):",
            user_hint.strip(),
        ])
    cal_hint = _calendar_block(prospect_reply_text)
    if cal_hint:
        parts.append(cal_hint)
    parts.extend([
        "",
        "Draft my response now. Output ONLY the reply body — no subject, no "
        "quoted-text, no signature block beyond a single 'Jaydip' at the end.",
    ])
    return "\n".join(parts)


# Lightweight regex-based sentiment classifier — runs synchronously during
# IMAP poll. Doesn't need the Bridge, so works 24/7. Buckets tuned for the
# 6 most common B2B reply shapes; anything it can't classify stays null and
# the UI shows a plain badge.
_SENTIMENT_RULES: list[tuple[str, "re.Pattern"]] = [
    ("ooo", re.compile(
        r"\b(out of office|on vacation|currently away|annual leave|"
        r"on holiday|away from my desk|will be back|limited access "
        r"to email)\b", re.IGNORECASE)),
    ("not_interested", re.compile(
        r"\b(not interested|unsubscribe|please remove|no thanks|"
        r"not a fit|don'?t contact|stop emailing|take me off your list|"
        r"we'?re all set|no need at this time|not looking|not hiring)\b",
        re.IGNORECASE)),
    ("question", re.compile(
        r"(\?\s*$)|\b(can you share|could you send|what is your|what's your|"
        r"how much|how do you|tell me more|more details|send me|"
        r"availability|your rate|pricing|quote)\b",
        re.IGNORECASE | re.MULTILINE)),
    ("positive", re.compile(
        r"\b(interested|sounds good|let'?s (talk|chat|connect|schedule|"
        r"set up)|schedule a call|book a call|jump on a call|"
        r"share (my|your) cv|forward (your|the) (cv|portfolio)|"
        r"add you to (our|my) network|keep you in (the )?loop|"
        r"great fit|exactly what|let me know (when|your) availabilit|"
        r"happy to (hop|connect|chat))\b", re.IGNORECASE)),
    ("referral", re.compile(
        r"\b(not me but|ping|forward|passing this to|reach out to|"
        r"talk to|contact our|my colleague|my team lead|right person)\b",
        re.IGNORECASE)),
]


def classify_sentiment(text: str) -> Optional[str]:
    """Return one of: positive | question | ooo | not_interested | referral
    | None (couldn't confidently classify)."""
    t = (text or "").strip()
    if not t:
        return None
    # Check in priority order — OOO first so "happy to chat when back" goes OOO.
    for label, pattern in _SENTIMENT_RULES:
        if pattern.search(t):
            return label
    return None


# More granular than sentiment — answers "what is the prospect actually
# asking for?" so the drawer can suggest the right reply template (or
# auto-fill profile facts in the form-fill case). Sentiment stays the
# coarse signal for routing; intent is the action-shape.
_INTENT_RULES: list[tuple[str, "re.Pattern[str]"]] = [
    ("form_fill", re.compile(
        r"(?:full\s*name|date\s*of\s*birth|dob)\s*:|"
        r"(?:current|expected)\s*ctc\s*:|"
        r"notice\s*period\s*:|"
        r"(?:total\s*)?experience\s*:|"
        r"mandatory\s*information",
        re.IGNORECASE,
    )),
    ("interview_request", re.compile(
        r"\b(schedule|book|set up|arrange|line up)\b.{0,40}\b(interview|call|chat|meeting)\b|"
        r"\b(interview|screening|technical\s*round)\b.{0,40}\b(slot|availability|time|when)\b|"
        r"\b(when|are\s*you)\s*(?:available|free)\s*(?:for|to)\b",
        re.IGNORECASE,
    )),
    ("scheduling", re.compile(
        r"\b(calendly|cal\.com|book\s*a\s*time|pick\s*a\s*slot)\b|"
        r"\b(send|share)\s*(?:your|me)?\s*(calendar|availability)\b",
        re.IGNORECASE,
    )),
    ("salary_question", re.compile(
        r"\b(rate|hourly|day\s*rate|monthly\s*rate|expected\s*ctc|"
        r"compensation|salary|budget|how\s*much)\b",
        re.IGNORECASE,
    )),
    ("referral", re.compile(
        r"\b(forward(?:ing)?|loop(?:ing)?\s*in|cc(?:'?ing)?|copying)\b|"
        r"\bspeak\s*with\s*(?:my|our)\s*(team|colleague|manager|hr)\b",
        re.IGNORECASE,
    )),
    ("info_request", re.compile(
        r"\b(send|share|share\s*me|provide)\b.{0,30}\b(cv|resume|portfolio|samples?|"
        r"profile|case\s*stud(?:y|ies))\b|"
        r"\b(more\s*info|tell\s*me\s*more|details)\b",
        re.IGNORECASE,
    )),
    ("rejection", re.compile(
        r"\b(not\s*(?:a\s*)?fit|won'?t\s*work\s*out|moving\s*on\s*with|"
        r"have\s*(?:filled|gone\s*with|selected)|already\s*hired|"
        r"position\s*(?:is\s*)?closed)\b",
        re.IGNORECASE,
    )),
]


def classify_intent(text: str) -> Optional[str]:
    """Granular intent label for an inbound reply. Distinct from
    sentiment — a positive sentiment might be 'interview_request' or
    'info_request' depending on what the prospect is actually asking
    for. Returns None when no rule fires confidently; the drawer falls
    back to generic templates in that case."""
    t = (text or "").strip()
    if not t:
        return None
    for label, pattern in _INTENT_RULES:
        if pattern.search(t):
            return label
    return None


def generate_reply_draft(
    *, prospect_first_name: str, prospect_reply_text: str,
    original_subject: str, original_body: str,
    user_hint: str = "",
    style_examples: Optional[list[dict]] = None,
) -> tuple[str, str]:
    """Returns (body, raw). If Bridge unreachable, body='' and raw has error.

    `user_hint` (optional): free-text instruction from Jaydip for this
    specific reply — Claude treats it as the highest-priority directive.
    `style_examples` (optional): list of {inbound, outbound} dicts from
    Jaydip's past sent replies — fed as few-shot style guidance so the
    drafter gradually matches his voice."""
    # Pin Jaydip's voice doc to the system prompt — it's the single
    # source of truth for tone/style/banned phrases. Few-shot examples
    # below still teach voice via demonstration; this doc keeps the
    # hard rules intact even when examples drift.
    voice = _voice_doc()
    system_prompt = _REPLY_SYSTEM_PROMPT
    if voice:
        system_prompt += (
            "\n\n--- JAYDIP'S VOICE (master reference, overrides any "
            "conflicting example) ---\n" + voice + "\n--- END VOICE DOC ---\n"
        )
    payload = {
        "system_prompt": system_prompt,
        "user_message": _reply_user_prompt(
            prospect_first_name=prospect_first_name,
            prospect_reply_text=prospect_reply_text,
            original_subject=original_subject,
            original_body=original_body,
            user_hint=user_hint,
            style_examples=style_examples,
        ),
    }
    try:
        r = requests.post(BRIDGE_URL, json=payload, timeout=BRIDGE_TIMEOUT_S)
        r.raise_for_status()
        reply = (r.json() or {}).get("reply", "")
    except requests.exceptions.RequestException as e:
        return "", f"(bridge unreachable: {str(e)[:200]})"
    return _strip_dashes(reply.strip()), reply
