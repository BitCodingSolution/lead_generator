"""
LinkedIn draft generator — calls the existing B2B Claude Bridge at
http://127.0.0.1:8765/generate-reply and returns a structured result:

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
import re
from dataclasses import dataclass
from typing import Optional

import requests

BRIDGE_URL = "http://127.0.0.1:8765/generate-reply"
BRIDGE_TIMEOUT_S = 180


class BridgeUnreachable(Exception):
    """Claude Bridge (localhost:8765) is offline or unreachable. Raised by
    generate_draft so the caller can refuse cleanly instead of falling back
    to a regex-only decision that might mis-archive a real lead."""


class BridgeParseError(Exception):
    """Bridge answered but the payload wasn't a parseable JSON object.
    Transient — the caller should surface as a retryable 502."""


def bridge_is_up(timeout: float = 1.5) -> bool:
    """Cheap health probe. Returns True if the Bridge answers on /. Used
    by batch-drafter preflight so we don't spawn a worker that would
    immediately refuse every lead."""
    try:
        r = requests.get("http://127.0.0.1:8765/", timeout=timeout)
        return 200 <= r.status_code < 500
    except requests.exceptions.RequestException:
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
        "- Plain text only. No HTML, no markdown.\n"
        "- NEVER use em-dashes or en-dashes. Use regular hyphens or periods.\n"
        "- No AI tone (\"I hope this finds you well\", \"I am writing to\", "
        "\"I wanted to reach out\"). Sound like a real person typed it.\n"
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
    """Enforce the em/en-dash ban from feedback memory."""
    return (s or "").replace("\u2014", "-").replace("\u2013", "-")


# --- public entrypoint -----------------------------------------------------


def generate_draft(
    *,
    posted_by: str = "",
    company: str = "",
    role: str = "",
    tech_stack: str = "",
    location: str = "",
    post_text: str = "",
) -> DraftResult:
    payload = {
        "system_prompt": _system_prompt(),
        "user_message": _user_prompt(
            posted_by=posted_by, company=company, role=role,
            tech_stack=tech_stack, location=location, post_text=post_text,
        ),
    }
    try:
        r = requests.post(BRIDGE_URL, json=payload, timeout=BRIDGE_TIMEOUT_S)
        r.raise_for_status()
        reply = (r.json() or {}).get("reply", "")
        data = _parse_json(reply)
    except requests.exceptions.RequestException as e:
        # Bridge offline / network down. We refuse to draft — a regex-only
        # skip decision could archive a genuine lead, and a drafter without
        # Claude's brain is worse than no draft at all. Caller (the route
        # handler) converts this to a user-visible 503.
        raise BridgeUnreachable(str(e)[:200]) from e
    except ValueError as e:
        # Bridge answered but returned garbage we couldn't parse. This is a
        # Claude/Bridge-side hiccup, not a connectivity issue — surface it
        # distinctly so the caller can retry the same lead safely.
        raise BridgeParseError(str(e)[:200]) from e
    subject = _strip_dashes(str(data.get("email_subject", "")).strip())
    body = _strip_dashes(str(data.get("email_body", "")).strip())
    mode = str(data.get("email_mode", "individual")).strip().lower()
    if mode not in ("individual", "company"):
        mode = "individual"
    should_skip = bool(data.get("should_skip", False))
    skip_reason = str(data.get("skip_reason", "")).strip() or None

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
    )


# --- reply drafter ---------------------------------------------------------


_REPLY_SYSTEM_PROMPT = (
    "You help draft short, professional replies to email responses received "
    "from B2B prospects. The agency (BitCoding Solutions, Surat India) sent "
    "a cold outreach; a prospect replied. Craft a warm, specific reply that "
    "moves the conversation forward.\n\n"
    "Hard rules:\n"
    "- 60-120 words MAX.\n"
    "- Plain text. No em-dashes (use hyphen). No AI-sounding phrases "
    "(\"I'd love to\", \"that sounds great\"). Write like a senior dev, not a "
    "marketer.\n"
    "- Reference something specific from their reply, don't give a generic "
    "acknowledgement.\n"
    "- If they asked a question, answer it concretely. If they asked for "
    "specifics (rate, availability, samples), give them or commit to a next "
    "step.\n"
    "- End with one low-friction next step (e.g., 'free for a 20-min call "
    "Tue/Wed?', 'happy to share the case study, just reply with yes').\n"
    "- Minimal signature: just 'Jaydip' — no company name / phone / taglines.\n"
    "- Output ONLY the reply body text. No subject line, no greeting boilerplate "
    "beyond 'Hi <firstname>,' (or '<firstname>,' is fine).\n"
)


def _reply_user_prompt(
    *, prospect_first_name: str, prospect_reply_text: str,
    original_subject: str, original_body: str,
    user_hint: str = "",
    style_examples: Optional[list[dict]] = None,
) -> str:
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
    payload = {
        "system_prompt": _REPLY_SYSTEM_PROMPT,
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
