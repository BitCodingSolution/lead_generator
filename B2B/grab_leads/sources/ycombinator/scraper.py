"""
Y Combinator companies scraper.

Hits YC's public Algolia index (same one the /companies page uses) — no auth,
no browser automation. Rich data: batch, industry, location, team_size,
isHiring flag, website, one_liner, long_description, tags.

Usage:
    python scraper.py --limit 100
    python scraper.py --hiring-only --us-only --limit 500
    python scraper.py --batch "Summer 2024"
    python scraper.py --industry B2B --us-only
    python scraper.py --dry-run --limit 5
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common.base_scraper import BaseScraper
# common.db (imported transitively by base_scraper) puts dashboard/backend
# on sys.path, so app.yc.models resolves here.
from app.yc.models import YcLead


ALGOLIA_URL = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/YCCompany_production/query"
ALGOLIA_APP_ID = "45BWZJ1SGC"


def _load_algolia_key() -> str:
    """Return the public search-only Algolia key for YC's /companies index.

    Read from `ALGOLIA_API_KEY` in the environment. If unset, fall back
    to parsing `dashboard/backend/.env` so the scraper works whether
    it's invoked from the FastAPI backend (env already loaded) or run
    standalone from this folder.
    """
    key = os.environ.get("ALGOLIA_API_KEY", "").strip().strip('"').strip("'")
    if key:
        return key
    # parents[3] = B2B repo root (this file is at B2B/grab_leads/sources/ycombinator/scraper.py)
    env_path = Path(__file__).resolve().parents[3] / "dashboard" / "backend" / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ALGOLIA_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(
        "ALGOLIA_API_KEY not set. Add it to dashboard/backend/.env "
        "(public search-only key from window.AlgoliaOpts on YC /companies)."
    )


ALGOLIA_API_KEY = _load_algolia_key()
PAGE_SIZE = 100  # Algolia hard-caps hitsPerPage; 100 is safe.


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url if "://" in url else f"http://{url}").hostname or ""
        return host.lower().removeprefix("www.") or None
    except Exception:
        return None


class YCScraper(BaseScraper):
    source_name = "ycombinator"
    leads_model = YcLead

    def _query_page(self, page: int, filters: str | None) -> dict:
        body = {"query": "", "hitsPerPage": PAGE_SIZE, "page": page}
        if filters:
            body["filters"] = filters
        headers = {
            "X-Algolia-Application-Id": ALGOLIA_APP_ID,
            "X-Algolia-API-Key": ALGOLIA_API_KEY,
            "Content-Type": "application/json",
        }
        r = requests.post(ALGOLIA_URL, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _build_filters(
        hiring_only: bool, us_only: bool, batch: str | None, industry: str | None
    ) -> str | None:
        parts = []
        if hiring_only:
            parts.append("isHiring:true")
        if us_only:
            parts.append('regions:"United States of America"')
        if batch:
            parts.append(f'batch:"{batch}"')
        if industry:
            parts.append(f'industry:"{industry}"')
        return " AND ".join(parts) if parts else None

    @staticmethod
    def _classify_signal(hit: dict) -> tuple[str, str]:
        if hit.get("isHiring"):
            return "yc_active_hiring", f"YC {hit.get('batch', '')} · actively hiring"
        batch = hit.get("batch") or ""
        m = re.match(r"(Winter|Summer|Spring|Fall)\s+(\d{4})", batch)
        if m and int(m.group(2)) >= 2023:
            return "yc_recent_batch", f"YC {batch}"
        return "yc_portfolio", f"YC {batch}" if batch else "YC portfolio"

    def _hit_to_lead(self, hit: dict) -> dict:
        signal_type, signal_detail = self._classify_signal(hit)
        return {
            "source_url": f"https://www.ycombinator.com/companies/{hit.get('slug', hit.get('objectID'))}",
            "company_name": hit.get("name") or "",
            "company_domain": _domain(hit.get("website")),
            "company_size": str(hit.get("team_size")) if hit.get("team_size") else None,
            "location": hit.get("all_locations"),
            "signal_type": signal_type,
            "signal_detail": signal_detail,
            # keep everything else under extra_data via base_scraper
            "yc_id": hit.get("id"),
            "slug": hit.get("slug"),
            "one_liner": hit.get("one_liner"),
            "long_description": hit.get("long_description"),
            "batch": hit.get("batch"),
            "status": hit.get("status"),
            "stage": hit.get("stage"),
            "industry": hit.get("industry"),
            "subindustry": hit.get("subindustry"),
            "industries": hit.get("industries"),
            "regions": hit.get("regions"),
            "tags": hit.get("tags"),
            "is_hiring": hit.get("isHiring"),
            "top_company": hit.get("top_company"),
            "website": hit.get("website"),
            "launched_at": hit.get("launched_at"),
            "team_size": hit.get("team_size"),
        }

    def scrape(
        self,
        limit: int | None = None,
        hiring_only: bool = False,
        us_only: bool = False,
        batch: str | None = None,
        industry: str | None = None,
        save_raw: bool = True,
        **_,
    ) -> Iterable[dict]:
        filters = self._build_filters(hiring_only, us_only, batch, industry)
        self.log.info("Filters: %s", filters or "(none)")
        seen = 0
        page = 0
        while True:
            self.log.info("Fetching page %d ...", page)
            data = self._query_page(page, filters)
            hits = data.get("hits", [])
            nb_pages = data.get("nbPages", 0)
            nb_hits = data.get("nbHits", 0)
            if page == 0:
                self.log.info("Total matches: %d (across %d pages)", nb_hits, nb_pages)
            if save_raw:
                self.save_raw(f"page_{page:04d}", data)
            for hit in hits:
                yield self._hit_to_lead(hit)
                seen += 1
                if limit and seen >= limit:
                    return
            page += 1
            if page >= nb_pages or not hits:
                return
            time.sleep(0.4)  # gentle pacing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Max companies to fetch")
    ap.add_argument("--hiring-only", action="store_true", help="Only actively hiring")
    ap.add_argument("--us-only", action="store_true", help="Only US-based")
    ap.add_argument("--batch", help='e.g. "Summer 2024"')
    ap.add_argument("--industry", help='e.g. "B2B", "Healthcare", "Fintech"')
    ap.add_argument("--dry-run", action="store_true", help="Preview, no DB writes")
    args = ap.parse_args()

    data_dir = Path(__file__).resolve().parent
    scraper = YCScraper(data_dir=data_dir)

    if args.dry_run:
        count = 0
        for lead in scraper.scrape(
            limit=args.limit or 5,
            hiring_only=args.hiring_only,
            us_only=args.us_only,
            batch=args.batch,
            industry=args.industry,
            save_raw=False,
        ):
            count += 1
            print(
                f"  [{count}] {lead['company_name']:<30}  "
                f"{(lead.get('company_domain') or '-'):<25}  "
                f"{lead['signal_type']:<20}  {lead.get('location') or '-'}"
            )
        print(f"\n[DRY RUN] Previewed {count} leads. No DB writes.")
        return

    stats = scraper.run(
        limit=args.limit,
        hiring_only=args.hiring_only,
        us_only=args.us_only,
        batch=args.batch,
        industry=args.industry,
    )
    print("\n=== SUMMARY ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"\nDB: postgres → {scraper.leads_model.__tablename__}")


if __name__ == "__main__":
    main()
