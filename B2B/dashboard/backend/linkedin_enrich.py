"""
Lightweight company enrichment for the LinkedIn drafter.

Given a company name, try to fetch its homepage and extract a short
"who they are" blurb. Cached per-company in `company_enrichment` so
repeated drafts for the same company don't repeatedly hit the network.

The drafter calls `enrich_company()` and gets back either:
  - a clean summary string (50-500 chars) → injected into the prompt
  - None → no signal, drafter falls back to post-text only

Failure modes are silent: a 404, timeout, JS-only page, or unguessable
domain just yields None and a long cooldown so we don't keep retrying.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

import requests

from linkedin_db import connect

# How long a stored entry stays valid before we'd re-fetch. Shortish so
# stale "we just rebranded" cases self-heal in a quarter.
TTL_DAYS = 90
# After a failed fetch (got nothing), wait this long before retrying so
# we're not hammering broken sites every draft.
EMPTY_RETRY_DAYS = 14
# Hard request budget — drafter's own latency budget is already tight,
# we'd rather skip enrichment than slow drafting to a crawl.
HTTP_TIMEOUT_S = 4.0
# Generous browser-style UA — some marketing sites 403 the default
# requests UA on principle.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


_SLUG_BAD = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Lower-case, drop punctuation, collapse runs of non-alphanum."""
    s = (name or "").lower().strip()
    s = _SLUG_BAD.sub("", s)
    return s


def _candidate_urls(company: str) -> list[str]:
    """Two-shot URL guess. We only try the most likely homepage forms;
    anything trickier needs a real lookup service we'd need an API key
    for. Drop common suffix words that bloat the slug ('inc', 'gmbh')."""
    base = (company or "").lower().strip()
    base = re.sub(
        r"\b(inc|inc\.|llc|ltd|ltd\.|gmbh|pvt|pvt\.|limited|corp|corp\.|co|co\.|"
        r"holdings|group|technologies|technology|tech|solutions|labs|"
        r"international|global|company)\b",
        "",
        base,
    )
    slug = _slugify(base)
    if not slug or len(slug) < 3:
        return []
    return [
        f"https://www.{slug}.com",
        f"https://{slug}.com",
    ]


_META_RE = re.compile(
    r'<meta\s+(?:[^>]*\bname=["\'](?:description|og:description)["\']'
    r'[^>]*\bcontent=["\']([^"\']+)["\']'
    r'|[^>]*\bcontent=["\']([^"\']+)["\']'
    r'[^>]*\bname=["\'](?:description|og:description)["\'])',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _extract_summary(html: str) -> Optional[str]:
    """Pull a usable blurb from a homepage. Strategy:
       1. <meta name="description"> (also og:description)
       2. <title> as a fallback
    Both sanitised + truncated to 500 chars."""
    if not html:
        return None
    m = _META_RE.search(html)
    if m:
        text = m.group(1) or m.group(2) or ""
        text = _WS_RE.sub(" ", text).strip()
        if 30 <= len(text) <= 500:
            return text
        if text:
            return text[:500]
    t = _TITLE_RE.search(html)
    if t:
        text = _WS_RE.sub(" ", t.group(1)).strip()
        if 5 <= len(text) <= 200:
            return text
    return None


def _fetch_one(url: str) -> Optional[tuple[str, str]]:
    """Returns (summary, url_used) on success, None on failure."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT_S,
                         allow_redirects=True)
    except requests.exceptions.RequestException:
        return None
    if r.status_code >= 400:
        return None
    summary = _extract_summary(r.text or "")
    if not summary:
        return None
    return summary, r.url


def enrich_company(company: Optional[str], *, force: bool = False) -> Optional[str]:
    """Public entrypoint. Returns a summary string or None.

    `force=True` skips the cache and re-fetches — useful when the user
    clicks a "Refresh" button on the lead drawer."""
    if not company or not company.strip():
        return None
    name = company.strip()
    now = dt.datetime.now()
    with connect() as con:
        if not force:
            row = con.execute(
                "SELECT summary, fetched_at FROM company_enrichment "
                "WHERE company = ?",
                (name,),
            ).fetchone()
            if row:
                fetched = dt.datetime.fromisoformat(row["fetched_at"])
                age = now - fetched
                if row["summary"]:
                    if age.days < TTL_DAYS:
                        return row["summary"]
                else:
                    # Empty summary stored — respect the empty-retry cooldown.
                    if age.days < EMPTY_RETRY_DAYS:
                        return None

    # Fetch fresh.
    summary: Optional[str] = None
    source: str = ""
    for url in _candidate_urls(name):
        result = _fetch_one(url)
        if result:
            summary, source = result
            break

    with connect() as con:
        con.execute(
            "INSERT INTO company_enrichment (company, summary, source, fetched_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(company) DO UPDATE SET "
            "  summary = excluded.summary, "
            "  source = excluded.source, "
            "  fetched_at = excluded.fetched_at",
            (name, summary or "", source, now.isoformat(timespec="seconds")),
        )
        con.commit()
    return summary or None
