"""Multi-source registry endpoints — list, detail, leads, facets, etc.

This is the schema-driven `/api/sources/*` namespace. Each registered
Source declares columns/filters/scraper-args via its own `schema.json`,
and the frontend renders per-source UI from those descriptors.

Storage
-------
Every grab source now lives in Postgres under per-source `<src>_*`
tables (e.g. ycombinator → `yc_leads`, `yc_founders`, `yc_exported_leads`).
Source.leads_table / Source.founders_table / Source.exported_table
control the dispatch — the legacy SQLite `data.db` path is gone.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.linkedin.db import connect as _pg_connect
from app.marcel.services.sources import all_sources, get_source

router = APIRouter(prefix="/api/sources", tags=["sources"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarize_grab(s) -> dict:
    """Counts + freshness for a grab source. Reads from Postgres."""
    leads_t = s.leads_table
    founders_t = s.founders_table
    if not leads_t:
        return {
            "leads_count": 0, "founders_count": 0, "verified_emails": 0,
            "companies_mailable": 0, "attention_count": 0,
            "last_scrape": None, "last_enrichment": None, "exists": False,
        }
    try:
        with _pg_connect() as con:
            leads = con.execute(f"SELECT COUNT(*) FROM {leads_t}").fetchone()[0]
            founders = 0
            verified = 0
            companies_mailable = 0
            last_enrich: Optional[str] = None
            if founders_t:
                founders = con.execute(
                    f"SELECT COUNT(*) FROM {founders_t}"
                ).fetchone()[0]
                verified = con.execute(
                    f"SELECT COUNT(*) FROM {founders_t} WHERE email_status = ?",
                    ("ok",),
                ).fetchone()[0]
                row = con.execute(
                    f"SELECT COUNT(DISTINCT lead_id) FROM {founders_t} "
                    f"WHERE email_status = ?",
                    ("ok",),
                ).fetchone()
                companies_mailable = row[0] if row else 0
                row = con.execute(
                    f"SELECT MAX(enriched_at) FROM {founders_t}"
                ).fetchone()
                last_enrich = row[0] if row else None
            row = con.execute(
                f"SELECT MAX(scraped_at) FROM {leads_t}"
            ).fetchone()
            last_scrape = row[0] if row else None
            row = con.execute(
                f"SELECT COUNT(*) FROM {leads_t} WHERE COALESCE(needs_attention, 0) = 1"
            ).fetchone()
            attention = row[0] if row else 0
        return {
            "leads_count": leads, "founders_count": founders,
            "verified_emails": verified,
            "companies_mailable": companies_mailable,
            "attention_count": attention,
            "last_scrape": last_scrape,
            "last_enrichment": last_enrich, "exists": True,
        }
    except Exception as e:
        return {"error": str(e), "exists": True}


def _summarize_outreach(s) -> dict:
    """Summary for the (Postgres-backed) Marcel outreach source.

    Reads from the `mrc_*` tables via the marcel `conn()` adapter — the
    legacy SQLite path was retired when the data migrated to Postgres,
    so `s.db_path` is now ignored for outreach sources.
    """
    from app.marcel.db import conn as _pg_conn  # local import: avoid cycle
    try:
        with _pg_conn() as con:
            total = con.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            emailed = con.execute(
                "SELECT COUNT(*) FROM emails_sent WHERE sent_at IS NOT NULL"
            ).fetchone()[0]
            # `replies` is its own table in the migrated schema; legacy
            # SQLite stored a `reply_received` flag on emails_sent. Use
            # the new dedicated table so the count reflects post-migration
            # truth.
            replies = con.execute("SELECT COUNT(*) FROM replies").fetchone()[0]
            last_sent = con.execute(
                "SELECT MAX(sent_at) FROM emails_sent"
            ).fetchone()[0]
        return {
            "leads_count": total, "emailed": emailed, "replies": replies,
            "last_sent": last_sent, "exists": True,
        }
    except Exception as e:
        return {"error": str(e), "exists": True}


def _summarize(s) -> dict:
    return _summarize_grab(s) if s.type == "grab" else _summarize_outreach(s)


def _shape_grab_row(row) -> dict:
    """Merge `extra_data` JSON into a nested 'extra' key for dot-path access."""
    d = dict(row)
    raw = d.pop("extra_data", None)
    try:
        d["extra"] = json.loads(raw) if raw else {}
    except Exception:
        d["extra"] = {}
    return d


# ---------------------------------------------------------------------------
# /api/sources — list + detail
# ---------------------------------------------------------------------------


@router.get("")
def list_sources() -> dict:
    out = []
    for s in all_sources().values():
        schema = s.load_schema()
        display = schema.get("display", {})
        out.append({
            "id": s.id,
            "label": display.get("label", s.label),
            "icon": display.get("icon", s.icon),
            "description": display.get("description", s.description),
            "type": s.type,
            "summary": _summarize(s),
        })
    return {"sources": out, "count": len(out)}


@router.get("/{source_id}")
def source_detail(source_id: str) -> dict:
    s = get_source(source_id)
    return {
        "id": s.id,
        "type": s.type,
        "schema": s.load_schema(),
        "summary": _summarize(s),
    }


# ---------------------------------------------------------------------------
# Per-source leads — Postgres JSON path expressions for `extra_data` filters
# ---------------------------------------------------------------------------

# JSON path access in Postgres: `(extra_data::jsonb ->> 'key')`. For ints,
# cast the result with `::int`. SQLite's `json_extract(...,'$.key')` maps
# 1:1 onto these snippets, so the filter logic below is a direct port.
_SORT_KEYS_TPL = {
    "id":         "{leads}.id",
    "company":    "{leads}.company_name",
    "team_size":  "({leads}.extra_data::jsonb ->> 'team_size')::int",
    "batch":      "({leads}.extra_data::jsonb ->> 'launched_at')",
    "scraped_at": "{leads}.scraped_at",
}


@router.get("/{source_id}/leads")
def source_leads(
    source_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    has_email: Optional[bool] = None,
    search: Optional[str] = None,
    batch: Optional[str] = None,
    industry: Optional[str] = None,
    stage: Optional[str] = None,
    team_min: Optional[int] = None,
    team_max: Optional[int] = None,
    top_only: Optional[bool] = None,
    hiring_only: Optional[bool] = None,
    exclude_exported: Optional[bool] = None,
    starred_only: Optional[bool] = None,
    attention_only: Optional[bool] = None,
    sort: str = "id",
    order: str = "desc",
) -> dict:
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(
            400,
            f"'/leads' endpoint is for grab-type sources "
            f"(source '{source_id}' is '{s.type}')",
        )
    if not s.leads_table:
        return {"rows": [], "total": 0, "limit": limit, "offset": offset}

    leads_t = s.leads_table
    founders_t = s.founders_table or ""
    exported_t = s.exported_table or ""

    where: list[str] = []
    params: list[Any] = []

    if has_email is True and founders_t:
        where.append(
            f"EXISTS (SELECT 1 FROM {founders_t} f "
            f"WHERE f.lead_id = {leads_t}.id AND f.email_status = 'ok')"
        )
    elif has_email is False and founders_t:
        where.append(
            f"NOT EXISTS (SELECT 1 FROM {founders_t} f "
            f"WHERE f.lead_id = {leads_t}.id AND f.email_status = 'ok')"
        )

    if search:
        where.append(
            f"({leads_t}.company_name ILIKE ? OR {leads_t}.company_domain ILIKE ? "
            f"OR ({leads_t}.extra_data::jsonb ->> 'one_liner') ILIKE ?)"
        )
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]

    if batch:
        where.append(f"({leads_t}.extra_data::jsonb ->> 'batch') = ?")
        params.append(batch)
    if industry:
        where.append(f"({leads_t}.extra_data::jsonb ->> 'industry') = ?")
        params.append(industry)
    if stage:
        where.append(f"({leads_t}.extra_data::jsonb ->> 'stage') = ?")
        params.append(stage)
    if team_min is not None:
        where.append(f"({leads_t}.extra_data::jsonb ->> 'team_size')::int >= ?")
        params.append(int(team_min))
    if team_max is not None:
        where.append(f"({leads_t}.extra_data::jsonb ->> 'team_size')::int <= ?")
        params.append(int(team_max))
    if top_only:
        # Stored as bool true in JSON; postgres comparison with 'true' literal.
        where.append(f"({leads_t}.extra_data::jsonb ->> 'top_company') IN ('1', 'true')")
    if hiring_only:
        where.append(f"({leads_t}.extra_data::jsonb ->> 'is_hiring') IN ('1', 'true')")
    if exclude_exported and exported_t:
        where.append(
            f"NOT EXISTS (SELECT 1 FROM {exported_t} e WHERE e.lead_id = {leads_t}.id)"
        )
    if starred_only:
        where.append(f"COALESCE({leads_t}.is_high_value, 0) = 1")
    if attention_only:
        where.append(f"COALESCE({leads_t}.needs_attention, 0) = 1")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    with _pg_connect() as con:
        total = con.execute(
            f"SELECT COUNT(*) FROM {leads_t} {where_sql}", tuple(params),
        ).fetchone()[0]

        if founders_t:
            mailable_where = list(where)
            mailable_where.append(
                f"EXISTS (SELECT 1 FROM {founders_t} f "
                f"WHERE f.lead_id = {leads_t}.id AND f.email_status = 'ok')"
            )
            mailable = con.execute(
                f"SELECT COUNT(*) FROM {leads_t} "
                f"WHERE {' AND '.join(mailable_where)}",
                tuple(params),
            ).fetchone()[0]
        else:
            mailable = 0

        sort_col = _SORT_KEYS_TPL.get(sort, "{leads}.id").format(leads=leads_t)
        order_sql = "ASC" if str(order).lower() == "asc" else "DESC"

        rows = con.execute(
            f"SELECT {leads_t}.* FROM {leads_t} {where_sql} "
            f"ORDER BY {sort_col} {order_sql}, {leads_t}.id DESC "
            f"LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        leads = [_shape_grab_row(r) for r in rows]

        if leads and exported_t:
            ids = [l["id"] for l in leads]
            ph = ",".join("?" * len(ids))
            exported_rows = con.execute(
                f"SELECT DISTINCT lead_id FROM {exported_t} "
                f"WHERE lead_id IN ({ph})",
                tuple(ids),
            ).fetchall()
            exported = {r[0] for r in exported_rows}
            for l in leads:
                l["already_exported"] = l["id"] in exported

        if leads and founders_t:
            ids = [l["id"] for l in leads]
            ph = ",".join("?" * len(ids))
            fnd = con.execute(
                f"SELECT lead_id, full_name, title, email, email_status, linkedin_url "
                f"FROM {founders_t} WHERE lead_id IN ({ph})",
                tuple(ids),
            ).fetchall()
            by_lead: dict[int, list] = {}
            for f in fnd:
                by_lead.setdefault(f["lead_id"], []).append(dict(f))
            for l in leads:
                l["founders"] = by_lead.get(l["id"], [])

    return {
        "rows": leads,
        "total": total,
        "mailable": mailable,
        "limit": limit,
        "offset": offset,
    }


@router.post("/{source_id}/leads/{lead_id}/star")
def source_star_lead(source_id: str, lead_id: int, body: dict) -> dict:
    """Toggle / set the is_high_value flag on a lead. Body: {"value": bool}."""
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Starring is only for grab sources")
    if not s.leads_table:
        raise HTTPException(404, "Source has no leads table configured")
    value = 1 if body.get("value") else 0
    with _pg_connect() as con:
        cur = con.execute(
            f"UPDATE {s.leads_table} SET is_high_value = ? WHERE id = ?",
            (value, lead_id),
        )
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"Lead {lead_id} not found")
    return {"ok": True, "lead_id": lead_id, "is_high_value": bool(value)}


@router.post("/{source_id}/selection-check")
def source_selection_check(source_id: str, body: dict) -> dict:
    s = get_source(source_id)
    ids = body.get("lead_ids") or []
    if not ids or s.type != "grab" or not s.founders_table:
        return {"total": 0, "ready": [], "needs_enrichment": [], "no_founders": []}
    founders_t = s.founders_table
    ph = ",".join("?" * len(ids))
    with _pg_connect() as con:
        has_founders = {
            r[0] for r in con.execute(
                f"SELECT DISTINCT lead_id FROM {founders_t} WHERE lead_id IN ({ph})",
                tuple(ids),
            ).fetchall()
        }
        has_verified = {
            r[0] for r in con.execute(
                f"SELECT DISTINCT lead_id FROM {founders_t} "
                f"WHERE email_status = 'ok' AND lead_id IN ({ph})",
                tuple(ids),
            ).fetchall()
        }
    ready = sorted(has_verified)
    needs_enrichment = sorted(i for i in ids if i in has_founders and i not in has_verified)
    no_founders = sorted(i for i in ids if i not in has_founders)
    return {
        "total": len(ids),
        "ready": ready,
        "needs_enrichment": needs_enrichment,
        "no_founders": no_founders,
    }


@router.get("/{source_id}/facets")
def source_facets(source_id: str) -> dict:
    s = get_source(source_id)
    if s.type != "grab" or not s.leads_table:
        return {"facets": {}}
    schema = s.load_schema()
    filters = (schema.get("display") or {}).get("filters", [])
    facet_keys = [f["key"] for f in filters if f.get("facet")]
    leads_t = s.leads_table
    out: dict[str, list[dict]] = {}
    with _pg_connect() as con:
        for key in facet_keys:
            if key.startswith("extra."):
                json_key = key[len("extra."):]
                rows = con.execute(
                    f"SELECT (extra_data::jsonb ->> ?) AS v, COUNT(*) AS c "
                    f"FROM {leads_t} "
                    f"WHERE (extra_data::jsonb ->> ?) IS NOT NULL "
                    f"GROUP BY v ORDER BY c DESC LIMIT 50",
                    (json_key, json_key),
                ).fetchall()
            else:
                rows = con.execute(
                    f"SELECT {key} AS v, COUNT(*) AS c FROM {leads_t} "
                    f"WHERE {key} IS NOT NULL GROUP BY v ORDER BY c DESC LIMIT 50"
                ).fetchall()
            out[key] = [{"value": r["v"], "count": r["c"]} for r in rows]
    return {"facets": out}
