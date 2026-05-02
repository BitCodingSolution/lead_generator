"""
English cold-email drafter for grab-source batches — Bridge (Claude Opus)
personalized per lead.

For each row in the batch Excel, builds a rich user_message with:
  - Founder first name + title
  - Company, domain, industry, stage, team size
  - One-liner, short description snippet
  - Signal (YC batch / hiring / etc.)
  - Tags, top_company flag

… and calls the local Bridge service (http://127.0.0.1:8765/generate-reply)
which proxies to Claude Opus 4.6 via the Max subscription. Gets back a
{subject, body} JSON and writes to the Excel.

Falls back to a static template if the Bridge is unreachable (flag --no-bridge
to force static mode).

Usage:
    python generate_drafts_en.py --file "<batch.xlsx>"
    python generate_drafts_en.py --file "<batch.xlsx>" --limit 3
    python generate_drafts_en.py --file "<batch.xlsx>" --force     (overwrite)
    python generate_drafts_en.py --file "<batch.xlsx>" --no-bridge (static only)
    python generate_drafts_en.py --file "<batch.xlsx>" --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import os

import pandas as pd
import requests


# Env-overridable. BRIDGE_BASE feeds both URLs so callers only set one var.
# Default matches the dashboard backend (linkedin_claude.py).
_BRIDGE_BASE = os.environ.get("BRIDGE_BASE", "http://127.0.0.1:8766")
BRIDGE_URL = f"{_BRIDGE_BASE}/generate-reply"
BRIDGE_HEALTH = f"{_BRIDGE_BASE}/health"

SIGNATURE = """\
Best,
Pradip Kachhadiya
Business Development, BitCoding Solutions
bitcodingsolutions.com\
"""


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

STYLE_SPECS = {
    "short_crisp": {
        "word_range": "40-55",
        "shape": (
            "ONE crisp observation tied to their product + ONE short proof "
            "line + ONE CTA. 2 paragraphs max. Very punchy, minimal adjectives."
        ),
    },
    "medium_story": {
        "word_range": "80-100",
        "shape": (
            "3 short paragraphs. Para 1: specific observation about what "
            "they're building and a likely backend pressure point. Para 2: "
            "ONE production proof line that maps to that pressure. Para 3: "
            "soft CTA."
        ),
    },
    "hook_question": {
        "word_range": "55-75",
        "shape": (
            "Open with a concrete question about their scale/architecture "
            "(not generic). Follow with one sentence of our proof. End with "
            "CTA. Question should read like a peer is actually curious."
        ),
    },
    "achievement_led": {
        "word_range": "60-80",
        "shape": (
            "Lead with our strongest relevant production number in ONE "
            "sentence, THEN tie it to their situation. Then a one-sentence "
            "question or CTA. No self-promotion beyond the opening fact."
        ),
    },
}


def _base_system_prompt() -> str:
    """Shared core rules. A per-style block is appended at call time."""
    return """\
You draft cold emails for BitCoding Solutions, a small AI-first backend
engineering shop (India) serving US founders. One email per request.

What we are (use sparingly, pick at most ONE proof line per email):
- Python, LangChain, RAG, FastAPI, AWS. AI-first backend only, no generic web.
- Principal Jaydip N: Upwork Top Rated Plus, 100% JSS, $100K+ earned.
- Production proof: multi-agent LangChain 50K+ calls/day 99.8% uptime;
  healthcare NLP cutting manual work 70%; real-time extraction 1M+ records/mo.
- $70/hr direct, $5K+ engagements preferred.

ABSOLUTE RULES — the email will be rejected if you break these.

1. Output strict JSON only, nothing else: {"subject": "...", "body": "..."}

2. FORBIDDEN CHARACTERS:
   - NEVER use em dash (—) or en dash (–). Use a comma, period, or "and".
   - NEVER use middle dot separator (·).
   - Plain hyphen (-) is fine only inside words (e.g. "real-time").

3. FORBIDDEN PHRASES (these scream AI):
   "I hope this finds you well", "reaching out", "just wanted to", "circling back",
   "I'd love to", "would love to", "happy to share", "excited to",
   "particularly", "specifically", "essentially", "that said",
   "comprehensive", "robust", "seamless", "leverage", "synergy".

4. Subject:
   - Max 50 characters.
   - Lowercase unless proper noun.
   - Concrete hook tied to THIS company (product, scale, batch, hiring).
   - No exclamation marks, no emojis.

5. Body requirements come from the STYLE block at the end of this prompt.
   Always start with "Hi {first_name},". Do NOT include a signature,
   the system appends one. No bullet lists. No compliments.
   No placeholders (no "[company]" leftovers). Tone: peer-to-peer founder,
   direct, slightly informal, American English.

6. Never fabricate (no fake customer names, headcounts, or press mentions).
   Only use facts present in the provided lead data.

Return ONLY the JSON object. No markdown fences.
"""


def build_system_prompt(style: str) -> str:
    spec = STYLE_SPECS.get(style, STYLE_SPECS["medium_story"])
    return (
        _base_system_prompt()
        + f"\n\nSTYLE: {style}\n"
        + f"Target length: {spec['word_range']} words TOTAL.\n"
        + f"Shape: {spec['shape']}\n"
    )


SYSTEM_PROMPT = _base_system_prompt()  # backwards-compat default


def _build_user_message(row: dict, ctx: dict) -> str:
    """Compose the specific lead facts the LLM should draft from."""
    lines = []
    lines.append(f"Recipient first name: {ctx['first_name']}")
    if row.get("name"):
        lines.append(f"Full name: {row['name']}")
    if row.get("title"):
        lines.append(f"Title: {row['title']}")
    lines.append(f"Company: {ctx['company']}")
    if row.get("domain"):
        lines.append(f"Domain: {row['domain']}")
    if ctx.get("one_liner"):
        lines.append(f"One-liner: {ctx['one_liner']}")
    if ctx.get("long_description"):
        lines.append(f"Description: {ctx['long_description'][:500]}")
    if ctx.get("industry"):
        lines.append(f"Industry: {ctx['industry']}")
    if ctx.get("stage"):
        lines.append(f"Stage: {ctx['stage']}")
    if ctx.get("team_size"):
        lines.append(f"Team size: {ctx['team_size']}")
    if ctx.get("batch"):
        lines.append(f"YC batch: {ctx['batch']}")
    if ctx.get("tags"):
        tags = ctx["tags"]
        if isinstance(tags, list):
            tags = ", ".join(map(str, tags[:8]))
        lines.append(f"Tags: {tags}")
    if ctx.get("is_hiring"):
        lines.append("Currently hiring (signal: they have budget to scale)")
    if ctx.get("top_company"):
        lines.append("Marked as top YC company")
    if ctx.get("source"):
        lines.append(f"Source: {ctx['source']}")

    lines.append("")
    lines.append("Write the email now, JSON only.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Static fallback templates
# ---------------------------------------------------------------------------

STATIC_SUBJECTS_YC = [
    "quick note on {company}",
    "a thought on {company}",
    "{company} — worth a look?",
]

STATIC_BODY = """\
Hi {first_name},

{company} caught my eye — {one_liner}. Teams at your stage often end up needing a senior AI/backend partner to move production features faster than a new hire can ramp.

We've shipped production AI systems for US startups: a multi-agent LangChain stack at 50K+ calls/day (99.8% uptime), and a real-time extraction pipeline at 1M+ records/month. Small team, $70/hr, no long commitment.

If there's a backlog that could use an extra pair of senior hands, happy to take a 20-min look.
"""


def _static_draft(ctx: dict, idx: int) -> tuple[str, str]:
    subj_tpl = STATIC_SUBJECTS_YC[idx % len(STATIC_SUBJECTS_YC)]
    subject = subj_tpl.format(company=ctx["company"])
    body = STATIC_BODY.format(
        first_name=ctx["first_name"],
        company=ctx["company"],
        one_liner=ctx.get("one_liner") or f"building in {ctx.get('industry') or 'B2B'}",
    )
    return subject, body


# ---------------------------------------------------------------------------
# Bridge client
# ---------------------------------------------------------------------------

def _bridge_up(timeout: float = 2.0) -> bool:
    try:
        r = requests.get(BRIDGE_HEALTH, timeout=timeout)
        return r.ok and (r.json() or {}).get("ok", False)
    except Exception:
        return False


_JSON_OBJ = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _parse_reply_json(text: str) -> dict | None:
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    # Fallback: grab the first {...} block
    m = _JSON_OBJ.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


_AI_TELLS_RE = re.compile(
    r"(I hope this finds you well|I wanted to reach out|circling back|"
    r"I'd love to|I would love to|would love to|happy to share|"
    r"particularly|specifically|essentially|comprehensive|"
    r"robust|seamless|leverage|synergy|in today's fast-paced)",
    re.IGNORECASE,
)


def _sanitize(text: str) -> str:
    """Enforce style rules regardless of what the model returned.
    Replaces banned characters with safe equivalents."""
    if not text:
        return text
    # Em/en dashes -> comma-space.  Handle varied unicode just in case.
    text = text.replace("—", ",").replace("–", ",").replace("\u2014", ",").replace("\u2013", ",")
    # Middle dot -> comma
    text = text.replace("·", ",").replace("\u00b7", ",")
    # Collapse ", ," runs and ",," etc.
    text = re.sub(r"\s*,\s*,+\s*", ", ", text)
    text = re.sub(r",\s+,", ",", text)
    # Trim extra spaces
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _call_bridge(row: dict, ctx: dict, style: str, retries: int = 2) -> tuple[str, str] | None:
    """Return (subject, body) on success, None on failure."""
    user_msg = _build_user_message(row, ctx)
    prompt = build_system_prompt(style)
    for attempt in range(retries + 1):
        try:
            r = requests.post(
                BRIDGE_URL,
                json={
                    "system_prompt": prompt,
                    "user_message": user_msg,
                    "max_turns": 1,
                },
                timeout=120,
            )
            r.raise_for_status()
            data = r.json()
            reply = data.get("reply") or data.get("text") or ""
            parsed = _parse_reply_json(reply)
            if parsed and parsed.get("subject") and parsed.get("body"):
                subj = _sanitize(str(parsed["subject"]))[:80]
                body = _sanitize(str(parsed["body"]))
                # Warn (don't fail) if AI-tell phrases slipped through — the
                # sanitizer removes chars but we keep phrase-level tells
                # visible in logs so prompt can be tuned.
                m = _AI_TELLS_RE.search(body)
                if m:
                    print(f"    [warn] AI-tell phrase detected: '{m.group(0)}' — consider prompt tune")
                return subj, body
        except Exception as e:
            if attempt >= retries:
                print(f"    Bridge error (attempt {attempt+1}): {e}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    return str(v).strip()


def _first_name(full_name: str) -> str:
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    return parts[0] if parts else "there"


def _extract_personalization(row) -> dict:
    raw = _s(row.get("personalization"))
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

STYLE_ORDER = ["short_crisp", "medium_story", "hook_question", "achievement_led"]


def _persona_of(title: str) -> str:
    t = (title or "").lower()
    if "ceo" in t:
        return "CEO"
    if "cto" in t:
        return "CTO"
    if "coo" in t:
        return "COO"
    if "founder" in t:
        return "Founder"
    if "vp" in t or "head" in t:
        return "VP/Head"
    return "Other"


def _team_bucket(size) -> str:
    try:
        n = int(size)
    except Exception:
        return "unknown"
    if n < 10: return "<10"
    if n < 50: return "10-50"
    if n < 200: return "50-200"
    if n < 500: return "200-500"
    if n < 2000: return "500-2000"
    return "2000+"


def process(file: Path, limit: int | None, force: bool, dry_run: bool, no_bridge: bool) -> dict:
    df = pd.read_excel(file)
    for col in ("draft_subject", "draft_body", "generated_at", "draft_language", "notes", "personalization", "email_tags"):
        if col not in df.columns:
            df[col] = ""

    mask_need = df["draft_subject"].map(_s) == ""
    if force:
        mask_need[:] = True
    todo_idx = df.index[mask_need].tolist()
    if limit:
        todo_idx = todo_idx[: int(limit)]

    print(f"Batch: {file.name}")
    print(f"Total rows: {len(df)}  |  Need drafts: {mask_need.sum()}  |  Processing: {len(todo_idx)}")

    use_bridge = (not no_bridge) and _bridge_up()
    print(f"Bridge: {'ONLINE (Claude Opus personalized)' if use_bridge else 'offline — using static fallback'}")

    stats = {"processed": 0, "bridge_ok": 0, "static_fallback": 0, "failed": 0}

    for i, idx in enumerate(todo_idx):
        row_s = {
            k: _s(df.at[idx, k])
            for k in ("lead_id", "name", "title", "company", "email", "domain",
                      "industry", "sub_industry", "notes", "source_file")
        }
        pers = _extract_personalization(df.loc[idx])
        ctx = {
            "first_name": _first_name(row_s["name"]),
            "company": row_s["company"] or "your company",
            "one_liner": pers.get("one_liner") or row_s["sub_industry"] or "building something interesting",
            "long_description": pers.get("long_description") or "",
            "industry": pers.get("industry") or row_s["industry"] or "B2B",
            "stage": pers.get("stage") or "",
            "team_size": pers.get("team_size") or "",
            "batch": pers.get("batch") or "",
            "tags": pers.get("tags") or [],
            "is_hiring": bool(pers.get("is_hiring")),
            "top_company": bool(pers.get("top_company")),
            "source": pers.get("source") or "ycombinator",
        }

        # Round-robin style per row so a batch ships with mixed variants.
        style = STYLE_ORDER[i % len(STYLE_ORDER)]

        subject, body, tpl_key = None, None, None
        if use_bridge:
            print(f"  [{i+1}/{len(todo_idx)}] {row_s['lead_id']:<10} {ctx['first_name']} @ {ctx['company']:<25} [{style}] -> Bridge…")
            result = _call_bridge(row_s, ctx, style)
            if result:
                subject, body = result
                tpl_key = f"bridge:{style}"
                stats["bridge_ok"] += 1
            else:
                stats["static_fallback"] += 1
                tpl_key = "static_fallback"

        if subject is None:
            subject, body = _static_draft(ctx, i)
            tpl_key = tpl_key or "static"

        # Append signature (LLM is instructed to NOT include it)
        full_body = body.rstrip() + "\n\n" + SIGNATURE

        # Tags — used later to correlate styles/personas with reply rate
        tags = {
            "style": style if use_bridge else "static",
            "persona": _persona_of(row_s.get("title")),
            "industry": ctx["industry"],
            "batch_year": ctx.get("batch") or "",
            "team_bucket": _team_bucket(ctx.get("team_size")),
            "hiring": bool(ctx.get("is_hiring")),
            "top_company": bool(ctx.get("top_company")),
            "word_count": len(body.split()),
            "tpl_key": tpl_key,
        }

        print(f"    -> [{tpl_key}] ({tags['word_count']}w) {subject[:70]}")

        if dry_run:
            continue

        df.at[idx, "draft_subject"] = subject
        df.at[idx, "draft_body"] = full_body
        df.at[idx, "draft_language"] = "en"
        df.at[idx, "generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        df.at[idx, "email_tags"] = json.dumps(tags, ensure_ascii=False)
        prev_notes = _s(df.at[idx, "notes"])
        df.at[idx, "notes"] = (prev_notes + (" | " if prev_notes else "") + f"tpl:{tpl_key}")[:500]
        stats["processed"] += 1

    if not dry_run and stats["processed"]:
        with pd.ExcelWriter(
            str(file),
            engine="xlsxwriter",
            engine_kwargs={"options": {"strings_to_urls": False}},
        ) as w:
            df.to_excel(w, sheet_name="Batch", index=False)
        print(f"\n[OK] Wrote {stats['processed']} drafts to {file.name}")
        print(f"     Bridge: {stats['bridge_ok']}  |  Static fallback: {stats['static_fallback']}")
    else:
        print("\n[DRY RUN] No writes." if dry_run else "\nNothing to write.")

    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="Overwrite existing drafts")
    ap.add_argument("--no-bridge", action="store_true", help="Skip Bridge, use static only")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        print(f"File not found: {p}", file=sys.stderr)
        sys.exit(1)

    process(p, args.limit, args.force, args.dry_run, args.no_bridge)


if __name__ == "__main__":
    main()
