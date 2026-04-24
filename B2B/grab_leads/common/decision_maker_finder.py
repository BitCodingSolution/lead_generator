"""
Decision-maker + company-meta finder — Phase 1 version.

Supports YC companies: hits the public detail page
(https://www.ycombinator.com/companies/{slug}) ONCE and extracts BOTH:
  - founder list (name, title, linkedin, twitter, bio, avatar)
  - rich company metadata (all socials, year founded, logo, city, demo
    day / app video URLs, YCDC page, primary group partner)

Everything ends up in the DB so future features (GitHub signal, investor
research via Crunchbase, product demo review via YC videos, etc.) don't
require a re-scrape.

Future (not built yet): generic /team /about scraper, LinkedIn Google dork.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests


YC_DETAIL_URL = "https://www.ycombinator.com/companies/{slug}"
DATA_PAGE_RE = re.compile(r'data-page="([^"]+)"')
UA = "Mozilla/5.0 (compatible; GrabLeadsBot/0.1; +bitcodingsolutions.com)"


@dataclass
class Person:
    full_name: str
    first_name: str = ""
    last_name: str = ""
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    bio: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class CompanyMeta:
    """Everything about the COMPANY (not a founder) pulled from the YC detail
    page. Stored into leads.extra_data during enrichment so it's queryable
    later without re-scraping."""
    # Socials
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    facebook_url: Optional[str] = None
    crunchbase_url: Optional[str] = None
    github_url: Optional[str] = None
    # Identity / branding
    logo_url: Optional[str] = None
    small_logo_url: Optional[str] = None
    # Location
    city: Optional[str] = None
    city_tag: Optional[str] = None
    country: Optional[str] = None
    location_full: Optional[str] = None
    # Timeline
    year_founded: Optional[int] = None
    ycdc_status: Optional[str] = None         # e.g. 'Active', 'Inactive', 'Acquired', 'Public'
    # Rich content
    ycdc_url: Optional[str] = None            # YC's own directory URL
    dday_video_url: Optional[str] = None      # Demo day video
    app_video_url: Optional[str] = None       # App video
    app_answers: Optional[dict] = None        # YC application answers if public
    free_response_question_answers: Optional[list] = None
    primary_group_partner: Optional[dict] = None
    photos: Optional[list] = None             # Company photos if present
    # Raw blob (trimmed) for anything we didn't promote to its own field
    raw_keys: list[str] = field(default_factory=list)


def _split_name(full: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (full or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _nullify_empty(v):
    """Coerce empty strings to None so we don't litter the DB with '' values."""
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def fetch_yc_detail(slug: str, timeout: int = 20, retries: int = 2) -> tuple[list[Person], CompanyMeta]:
    """Single HTTP fetch — returns (founders, company_meta)."""
    url = YC_DETAIL_URL.format(slug=slug)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
            r.raise_for_status()
            break
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(f"YC detail fetch failed for {slug}: {last_err}")

    m = DATA_PAGE_RE.search(r.text)
    if not m:
        return [], CompanyMeta()
    try:
        data = json.loads(htmllib.unescape(m.group(1)))
    except json.JSONDecodeError:
        return [], CompanyMeta()

    company = (data.get("props") or {}).get("company") or {}

    # ---- Company meta ----
    meta = CompanyMeta(
        linkedin_url=_nullify_empty(company.get("linkedin_url")),
        twitter_url=_nullify_empty(company.get("twitter_url")),
        facebook_url=_nullify_empty(company.get("fb_url")),
        crunchbase_url=_nullify_empty(company.get("cb_url")),
        github_url=_nullify_empty(company.get("github_url")),
        logo_url=_nullify_empty(company.get("logo_url")),
        small_logo_url=_nullify_empty(company.get("small_logo_url")),
        city=_nullify_empty(company.get("city")),
        city_tag=_nullify_empty(company.get("city_tag")),
        country=_nullify_empty(company.get("country")),
        location_full=_nullify_empty(company.get("location")),
        year_founded=company.get("year_founded"),
        ycdc_status=_nullify_empty(company.get("ycdc_status")),
        ycdc_url=_nullify_empty(company.get("ycdc_url")),
        dday_video_url=_nullify_empty(company.get("dday_video_url")),
        app_video_url=_nullify_empty(company.get("app_video_url")),
        app_answers=company.get("app_answers"),
        free_response_question_answers=company.get("free_response_question_answers"),
        primary_group_partner=company.get("primary_group_partner"),
        photos=company.get("company_photos"),
        raw_keys=sorted(company.keys()),
    )

    # ---- Founders ----
    founders: list[Person] = []
    for f in (company.get("founders") or []):
        if not f.get("is_active", True):
            continue
        full = (f.get("full_name") or "").strip()
        if not full:
            continue
        first, last = _split_name(full)
        founders.append(
            Person(
                full_name=full,
                first_name=first,
                last_name=last,
                title=f.get("title"),
                linkedin_url=_nullify_empty(f.get("linkedin_url")),
                twitter_url=_nullify_empty(f.get("twitter_url")),
                bio=f.get("founder_bio"),
                extra={
                    "yc_user_id": f.get("user_id"),
                    "avatar_url": f.get("avatar_thumb_url"),
                },
            )
        )
    return founders, meta


# Backwards-compatible helper (kept so old callers that only want founders
# keep working).
def fetch_yc_founders(slug: str, **kw) -> list[Person]:
    founders, _ = fetch_yc_detail(slug, **kw)
    return founders
