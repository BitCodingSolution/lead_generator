"""
Enrichment orchestrator: lead (company) -> founders -> email candidates -> verify.

Reads companies via the backend's SQLAlchemy ORM (`YcLead`) and writes
results to `YcFounder`. Schema for both is owned by SQLAlchemy / Alembic
on the backend; this script just consumes the models.

Usage:
    python common/enrich.py --source ycombinator --limit 10
    python common/enrich.py --source ycombinator --limit 10 --dry-run
    python common/enrich.py --source ycombinator --only-missing

Per-source model mapping is centralised in `_SOURCE_MODELS` below — add a
new source by appending one line.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import smtp_verify
from common.db import session_scope
from common.decision_maker_finder import fetch_yc_detail, CompanyMeta
from common.email_pattern_gen import generate as gen_emails

# Imported via common.db's sys.path bridge.
from app.yc.models import YcFounder, YcLead


# Per-source ORM models. Keys here must match what `app.main` registers
# as `Source.id`.
_SOURCE_MODELS: dict[str, tuple[type, type]] = {
    "ycombinator": (YcLead, YcFounder),
}


def _slug_from_extra(extra_json: str | None) -> str | None:
    if not extra_json:
        return None
    try:
        return json.loads(extra_json).get("slug")
    except Exception:
        return None


def _pick_best_email(candidates: list[str]) -> tuple[dict | None, list[dict]]:
    """Verify candidates in order; return first 'ok' verdict + all tried."""
    tried: list[dict] = []
    best: dict | None = None
    for email in candidates:
        verdict = smtp_verify.verify(email)
        tried.append(verdict)
        if verdict["status"] == "ok" and best is None:
            best = verdict
            break
    return best, tried


def _fetch_for_source(source: str, extra_data: str | None):
    """Return (founders, company_meta_or_None) for a given source."""
    if source == "ycombinator":
        slug = _slug_from_extra(extra_data)
        if not slug:
            return [], None
        return fetch_yc_detail(slug)
    raise NotImplementedError(f"No fetcher for source '{source}'")


def _merge_company_meta(lead, meta: CompanyMeta) -> None:
    """Merge CompanyMeta fields into the lead's extra_data JSON blob in place."""
    if meta is None:
        return
    try:
        existing = json.loads(lead.extra_data or "{}")
    except Exception:
        existing = {}
    meta_dict = {k: v for k, v in asdict(meta).items() if v not in (None, [], {})}
    # Namespace company meta under a single key to avoid clashing with
    # scraper-originated fields (industry, tags, etc.).
    existing["company_meta"] = {**(existing.get("company_meta") or {}), **meta_dict}
    lead.extra_data = json.dumps(existing, ensure_ascii=False)


def enrich(
    source: str,
    limit: int | None = None,
    only_missing: bool = True,
    dry_run: bool = False,
    sleep_between: float = 0.5,
) -> dict:
    if source not in _SOURCE_MODELS:
        raise SystemExit(
            f"Unknown source '{source}' (registered: {list(_SOURCE_MODELS)})"
        )
    LeadModel, FounderModel = _SOURCE_MODELS[source]

    # 1) Snapshot the leads to process. We don't hold a session open across
    #    the per-lead network I/O — each lead opens its own short-lived
    #    transaction below.
    with session_scope() as session:
        if only_missing:
            stmt = (
                select(LeadModel)
                .outerjoin(FounderModel, FounderModel.lead_id == LeadModel.id)
                .where(FounderModel.id.is_(None))
            )
        else:
            stmt = select(LeadModel)
        if limit:
            stmt = stmt.limit(int(limit))
        leads = session.execute(stmt).scalars().all()
        snapshots = [
            {
                "id": l.id,
                "company_name": l.company_name,
                "company_domain": l.company_domain,
                "extra_data": l.extra_data,
            }
            for l in leads
        ]

    stats = {
        "companies_processed": 0, "founders_found": 0,
        "emails_verified": 0, "errors": 0,
    }
    print(f"Processing {len(snapshots)} companies from {source}...")

    for snap in snapshots:
        stats["companies_processed"] += 1
        lead_id = snap["id"]
        name = snap["company_name"]
        domain = snap["company_domain"]
        try:
            people, meta = _fetch_for_source(source, snap["extra_data"])
        except Exception as e:
            stats["errors"] += 1
            print(f"  [{lead_id}] {name}: fetch error: {e}")
            continue

        # Persist company meta (socials, year_founded, YC videos, etc.) to the
        # lead row — even if no founders, meta is still valuable.
        if meta and not dry_run:
            try:
                with session_scope() as session:
                    lead = session.get(LeadModel, lead_id)
                    if lead is not None:
                        _merge_company_meta(lead, meta)
            except Exception as e:
                print(f"  [{lead_id}] {name}: meta merge failed: {e}")

        if not people:
            print(f"  [{lead_id}] {name}: no founders found (meta stored)")
            continue

        for p in people:
            stats["founders_found"] += 1
            candidates = (
                gen_emails(p.first_name, p.last_name, domain or "") if domain else []
            )
            best, tried = _pick_best_email(candidates)
            if best:
                stats["emails_verified"] += 1
            print(
                f"  [{lead_id}] {name:<25}  {p.full_name:<25}  {p.title or '-':<20}  "
                f"{(best['email'] if best else '— no verified —')}"
            )
            if dry_run:
                continue

            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                with session_scope() as session:
                    # Equivalent of the previous `ON CONFLICT (lead_id, full_name)
                    # DO NOTHING` — uniqueness is enforced by the unique index
                    # on (lead_id, full_name); we skip the insert if a row exists.
                    already_present = session.execute(
                        select(FounderModel.id).where(
                            FounderModel.lead_id == lead_id,
                            FounderModel.full_name == p.full_name,
                        )
                    ).scalar_one_or_none()
                    if already_present is not None:
                        continue
                    session.add(FounderModel(
                        lead_id=lead_id,
                        full_name=p.full_name,
                        first_name=p.first_name,
                        last_name=p.last_name,
                        title=p.title,
                        linkedin_url=p.linkedin_url,
                        twitter_url=p.twitter_url,
                        bio=p.bio,
                        email=best["email"] if best else None,
                        email_status=(
                            best["status"] if best
                            else (tried[-1]["status"] if tried else "no_domain")
                        ),
                        email_mx=best["mx_host"] if best else None,
                        candidates_tried=json.dumps(tried, ensure_ascii=False),
                        extra_data=(
                            json.dumps(p.extra, ensure_ascii=False) if p.extra else None
                        ),
                        enriched_at=now,
                    ))
            except Exception as e:
                print(f"  [{lead_id}] {name}: founder insert failed: {e}")

        time.sleep(sleep_between)

    print("\n=== ENRICHMENT SUMMARY ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="e.g. ycombinator")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-missing", action="store_true", default=True,
                    help="Skip leads that already have founders (default: on)")
    ap.add_argument("--all", action="store_true",
                    help="Also re-process leads with founders")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.5)
    args = ap.parse_args()

    enrich(
        source=args.source,
        limit=args.limit,
        only_missing=not args.all,
        dry_run=args.dry_run,
        sleep_between=args.sleep,
    )


if __name__ == "__main__":
    main()
