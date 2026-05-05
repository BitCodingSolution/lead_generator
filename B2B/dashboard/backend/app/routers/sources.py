"""Multi-source registry endpoints — list, detail, leads, facets, etc.

This is the schema-driven `/api/sources/*` namespace. Each registered
Source declares columns/filters/scraper-args via its own `schema.json`,
and the frontend renders per-source UI from those descriptors.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.sources import all_sources, get_source

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _count(con: sqlite3.Connection, table: str, where: str = "") -> int:
    if not _table_exists(con, table):
        return 0
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        return con.execute(sql).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _max_ts(con: sqlite3.Connection, table: str, col: str) -> Optional[str]:
    if not _table_exists(con, table):
        return None
    try:
        row = con.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def _summarize_grab(s) -> dict:
    if not s.db_path.exists():
        return {
            "leads_count": 0, "founders_count": 0, "verified_emails": 0,
            "companies_mailable": 0,
            "last_scrape": None, "last_enrichment": None, "exists": False,
        }
    try:
        con = _conn(s.db_path)
        leads = _count(con, "leads")
        founders = _count(con, "founders")
        verified = _count(con, "founders", "email_status='ok'")
        companies_mailable = 0
        if _table_exists(con, "founders"):
            row = con.execute(
                "SELECT COUNT(DISTINCT lead_id) FROM founders WHERE email_status='ok'"
            ).fetchone()
            companies_mailable = row[0] if row else 0
        last_scrape = _max_ts(con, "leads", "scraped_at")
        last_enrich = _max_ts(con, "founders", "enriched_at")
        attention = 0
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM leads WHERE COALESCE(needs_attention,0)=1"
            ).fetchone()
            attention = row[0] if row else 0
        except Exception:
            pass
        con.close()
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
    if not s.db_path.exists():
        return {"leads_count": 0, "exists": False}
    try:
        con = _conn(s.db_path)
        total = _count(con, "leads")
        emailed = _count(con, "emails_sent", "sent_at IS NOT NULL")
        replies = _count(con, "emails_sent", "reply_received=1") if _table_exists(con, "emails_sent") else 0
        last_sent = _max_ts(con, "emails_sent", "sent_at")
        con.close()
        return {
            "leads_count": total, "emailed": emailed, "replies": replies,
            "last_sent": last_sent, "exists": True,
        }
    except Exception as e:
        return {"error": str(e), "exists": True}


def _summarize(s) -> dict:
    return _summarize_grab(s) if s.type == "grab" else _summarize_outreach(s)


def _shape_grab_row(row: sqlite3.Row) -> dict:
    """Merge `extra_data` JSON into a nested 'extra' key for dot-path access."""
    d = dict(row)
    raw = d.pop("extra_data", None)
    try:
        d["extra"] = json.loads(raw) if raw else {}
    except Exception:
        d["extra"] = {}
    return d


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


EXPORTED_DDL = """
CREATE TABLE IF NOT EXISTS exported_leads (
    lead_id       INTEGER NOT NULL,
    founder_id    INTEGER,
    batch_file    TEXT,
    exported_at   TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (lead_id, founder_id)
);
CREATE INDEX IF NOT EXISTS idx_exported_lead ON exported_leads(lead_id);
"""


def _ensure_high_value_col(con: sqlite3.Connection) -> None:
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(leads)")}
    except sqlite3.OperationalError:
        return
    if "is_high_value" not in cols:
        try:
            con.execute("ALTER TABLE leads ADD COLUMN is_high_value INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass


_SORT_KEYS = {
    "id": "leads.id",
    "company": "leads.company_name",
    "team_size": "CAST(json_extract(leads.extra_data,'$.team_size') AS INTEGER)",
    "batch": "json_extract(leads.extra_data,'$.launched_at')",
    "scraped_at": "leads.scraped_at",
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
        raise HTTPException(400, f"'/leads' endpoint is for grab-type sources (source '{source_id}' is '{s.type}')")
    if not s.db_path.exists():
        return {"rows": [], "total": 0, "limit": limit, "offset": offset}

    con = _conn(s.db_path)
    con.executescript(EXPORTED_DDL)
    _ensure_high_value_col(con)

    where: list[str] = []
    params: list[Any] = []

    if has_email is True:
        where.append("EXISTS (SELECT 1 FROM founders f WHERE f.lead_id=leads.id AND f.email_status='ok')")
    elif has_email is False:
        where.append("NOT EXISTS (SELECT 1 FROM founders f WHERE f.lead_id=leads.id AND f.email_status='ok')")

    if search:
        where.append(
            "(leads.company_name LIKE ? OR leads.company_domain LIKE ? "
            "OR json_extract(leads.extra_data,'$.one_liner') LIKE ?)"
        )
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]

    if batch:
        where.append("json_extract(leads.extra_data,'$.batch') = ?")
        params.append(batch)
    if industry:
        where.append("json_extract(leads.extra_data,'$.industry') = ?")
        params.append(industry)
    if stage:
        where.append("json_extract(leads.extra_data,'$.stage') = ?")
        params.append(stage)
    if team_min is not None:
        where.append("CAST(json_extract(leads.extra_data,'$.team_size') AS INTEGER) >= ?")
        params.append(int(team_min))
    if team_max is not None:
        where.append("CAST(json_extract(leads.extra_data,'$.team_size') AS INTEGER) <= ?")
        params.append(int(team_max))
    if top_only:
        where.append("json_extract(leads.extra_data,'$.top_company') = 1")
    if hiring_only:
        where.append("json_extract(leads.extra_data,'$.is_hiring') = 1")
    if exclude_exported:
        where.append("NOT EXISTS (SELECT 1 FROM exported_leads e WHERE e.lead_id=leads.id)")
    if starred_only:
        where.append("COALESCE(leads.is_high_value, 0) = 1")
    if attention_only:
        where.append("COALESCE(leads.needs_attention, 0) = 1")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total = con.execute(f"SELECT COUNT(*) FROM leads {where_sql}", params).fetchone()[0]

    mailable_where = list(where)
    mailable_where.append(
        "EXISTS (SELECT 1 FROM founders f WHERE f.lead_id=leads.id AND f.email_status='ok')"
    )
    mailable = con.execute(
        f"SELECT COUNT(*) FROM leads WHERE {' AND '.join(mailable_where)}", params
    ).fetchone()[0]

    sort_col = _SORT_KEYS.get(sort, "leads.id")
    order_sql = "ASC" if str(order).lower() == "asc" else "DESC"

    rows = con.execute(
        f"SELECT leads.* FROM leads {where_sql} "
        f"ORDER BY {sort_col} {order_sql}, leads.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    leads = [_shape_grab_row(r) for r in rows]

    if leads:
        ids = tuple(l["id"] for l in leads)
        ph = ",".join("?" * len(ids))
        exported = {r[0] for r in con.execute(
            f"SELECT DISTINCT lead_id FROM exported_leads WHERE lead_id IN ({ph})", ids
        ).fetchall()}
        for l in leads:
            l["already_exported"] = l["id"] in exported

    if leads and _table_exists(con, "founders"):
        ids = tuple(l["id"] for l in leads)
        placeholders = ",".join("?" * len(ids))
        fnd = con.execute(
            f"SELECT lead_id, full_name, title, email, email_status, linkedin_url "
            f"FROM founders WHERE lead_id IN ({placeholders})",
            ids,
        ).fetchall()
        by_lead: dict[int, list] = {}
        for f in fnd:
            by_lead.setdefault(f["lead_id"], []).append(dict(f))
        for l in leads:
            l["founders"] = by_lead.get(l["id"], [])

    con.close()
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
    if not s.db_path.exists():
        raise HTTPException(404, "Source DB does not exist yet")
    value = 1 if body.get("value") else 0
    con = _conn(s.db_path)
    try:
        _ensure_high_value_col(con)
        cur = con.execute(
            "UPDATE leads SET is_high_value=? WHERE id=?", (value, lead_id)
        )
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"Lead {lead_id} not found")
    finally:
        con.close()
    return {"ok": True, "lead_id": lead_id, "is_high_value": bool(value)}


@router.post("/{source_id}/selection-check")
def source_selection_check(source_id: str, body: dict) -> dict:
    s = get_source(source_id)
    ids = body.get("lead_ids") or []
    if not ids or s.type != "grab" or not s.db_path.exists():
        return {"total": 0, "ready": [], "needs_enrichment": [], "no_founders": []}
    con = _conn(s.db_path)
    try:
        ph = ",".join("?" * len(ids))
        has_founders = {
            r[0] for r in con.execute(
                f"SELECT DISTINCT lead_id FROM founders WHERE lead_id IN ({ph})", ids
            ).fetchall()
        } if _table_exists(con, "founders") else set()
        has_verified = {
            r[0] for r in con.execute(
                f"SELECT DISTINCT lead_id FROM founders "
                f"WHERE email_status='ok' AND lead_id IN ({ph})", ids
            ).fetchall()
        } if _table_exists(con, "founders") else set()
    finally:
        con.close()

    ready = sorted(has_verified)
    needs_enrichment = sorted([i for i in ids if i in has_founders and i not in has_verified])
    no_founders = sorted([i for i in ids if i not in has_founders])
    return {
        "total": len(ids),
        "ready": ready,
        "needs_enrichment": needs_enrichment,
        "no_founders": no_founders,
    }


@router.get("/{source_id}/facets")
def source_facets(source_id: str) -> dict:
    s = get_source(source_id)
    if s.type != "grab" or not s.db_path.exists():
        return {"facets": {}}
    schema = s.load_schema()
    filters = (schema.get("display") or {}).get("filters", [])
    facet_keys = [f["key"] for f in filters if f.get("facet")]
    out: dict[str, list[dict]] = {}
    con = _conn(s.db_path)
    try:
        for key in facet_keys:
            if key.startswith("extra."):
                path = "$." + key[len("extra."):]
                rows = con.execute(
                    "SELECT json_extract(extra_data, ?) AS v, COUNT(*) AS c "
                    "FROM leads WHERE v IS NOT NULL GROUP BY v ORDER BY c DESC LIMIT 50",
                    (path,),
                ).fetchall()
            else:
                rows = con.execute(
                    f"SELECT {key} AS v, COUNT(*) AS c FROM leads "
                    f"WHERE v IS NOT NULL GROUP BY v ORDER BY c DESC LIMIT 50"
                ).fetchall()
            out[key] = [{"value": r["v"], "count": r["c"]} for r in rows]
    finally:
        con.close()
    return {"facets": out}
