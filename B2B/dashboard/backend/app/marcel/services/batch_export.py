"""Export a grab-source's verified-email leads into a Marcel-compatible
xlsx batch, ready for the drafter / Outlook writer.

This is the in-process callable used both by the HTTP endpoint and the
multi-step Campaign chain job — pulling the logic out of the legacy
main.py so both call sites share one implementation.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException

from app.config import settings
from app.linkedin.db import connect as _pg_connect
from app.marcel.services.sources import get_source


def title_priority(title: str) -> int:
    """Lower number = higher priority recipient. CEO/Founder > CTO > other."""
    t = (title or "").lower()
    if "ceo" in t and "founder" in t:
        return 0
    if "ceo" in t:
        return 1
    if "founder" in t and "board" not in t:
        return 2
    if "coo" in t:
        return 3
    if "cto" in t:
        return 4
    if "chief" in t or "chair" in t:
        return 5
    if "vp" in t or "head" in t:
        return 6
    return 9


def export_batch_core(
    source_id: str,
    lead_ids: Iterable[int] | None = None,
    industry_tag: str = "YC Portfolio",
    tier: int = 1,
    max_rows: int = 100,
    group_by_company: bool = True,
) -> dict:
    """Write an xlsx batch from a grab source's `founders` table.

    `group_by_company=True` collapses multiple founders at the same
    company into one row: primary recipient by title priority (CEO >
    Founder > CTO), co-founders placed in BCC.
    """
    import pandas as pd

    s = get_source(source_id)
    if s.type != "grab":
        raise RuntimeError("Export is only for grab sources")
    if not s.leads_table or not s.founders_table:
        raise RuntimeError(
            f"Source '{source_id}' has no leads/founders tables configured."
        )

    lead_ids = list(lead_ids) if lead_ids else None
    leads_t = s.leads_table
    founders_t = s.founders_table

    where = ["f.email_status = 'ok'"]
    params: list = []
    if lead_ids:
        placeholders = ",".join("?" * len(lead_ids))
        where.append(f"l.id IN ({placeholders})")
        params += lead_ids
    sql = (
        f"SELECT l.id as company_id, f.id as founder_id, "
        f"       l.company_name, l.company_domain, l.location, l.extra_data, "
        f"       f.full_name, f.title, f.email, f.linkedin_url "
        f"FROM {leads_t} l "
        f"JOIN {founders_t} f ON f.lead_id = l.id "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY l.id, f.id "
        f"LIMIT ?"
    )
    with _pg_connect() as con:
        rows = con.execute(sql, (*params, int(max_rows))).fetchall()

    if not rows:
        raise RuntimeError("No leads with verified emails match the selection")

    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["company_id"], []).append(dict(r))
    if group_by_company:
        chosen: list[tuple[dict, list[dict]]] = []
        for _cid, members in grouped.items():
            members.sort(key=lambda m: title_priority(m.get("title") or ""))
            primary, others = members[0], members[1:]
            chosen.append((primary, others))
        rows_to_write = chosen
    else:
        rows_to_write = [(dict(r), []) for r in rows]

    today = dt.date.today().isoformat()
    records: list[dict] = []
    all_exported_members: list[tuple[int, int]] = []
    for primary, cofounders in rows_to_write:
        r = primary
        extra = json.loads(r["extra_data"] or "{}")
        industry = extra.get("industry") or industry_tag
        meta = extra.get("company_meta") or {}
        personalization = {
            "one_liner": extra.get("one_liner"),
            "long_description": (extra.get("long_description") or "")[:800],
            "batch": extra.get("batch"),
            "stage": extra.get("stage"),
            "team_size": extra.get("team_size"),
            "tags": extra.get("tags"),
            "is_hiring": extra.get("is_hiring"),
            "top_company": extra.get("top_company"),
            "year_founded": meta.get("year_founded"),
            "source": source_id,
            "company_linkedin": meta.get("linkedin_url"),
            "company_twitter": meta.get("twitter_url"),
            "company_github": meta.get("github_url"),
            "company_crunchbase": meta.get("crunchbase_url"),
            "company_facebook": meta.get("facebook_url"),
            "person_linkedin": r["linkedin_url"],
        }
        records.append({
            "lead_id": f"{source_id[:2].upper()}{r['founder_id']:06d}",
            "name": r["full_name"],
            "salutation": "",
            "title": r["title"] or "",
            "company": r["company_name"],
            "email": r["email"],
            "phone": "",
            "xing": "",
            "linkedin": r["linkedin_url"] or "",
            "industry": industry,
            "sub_industry": extra.get("subindustry") or "",
            "domain": r["company_domain"] or "",
            "website": extra.get("website") or "",
            "city": r["location"] or "",
            "dealfront_link": "",
            "source_file": f"{source_id}:db",
            "tier": int(tier),
            "is_owner": 1,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "email_valid": 1,
            "email_invalid_reason": "ok",
            "email_verified_at": dt.datetime.now().isoformat(timespec="seconds"),
            "status": "New",
            "batch_date": today,
            "draft_subject": "",
            "draft_body": "",
            "draft_language": "en",
            "generated_at": "",
            "outlook_entry_id": "",
            "sent_at": "",
            "notes": f"Imported from source={source_id}",
            "personalization": json.dumps(personalization, ensure_ascii=False),
            "bcc": ", ".join(cf["email"] for cf in cofounders if cf.get("email")),
            "bcc_names": ", ".join(cf["full_name"] for cf in cofounders if cf.get("full_name")),
        })
        all_exported_members.append((r["company_id"], r["founder_id"]))
        for cf in cofounders:
            all_exported_members.append((cf["company_id"], cf["founder_id"]))

    out_dir = settings.grab_batches_dir
    out_path = out_dir / f"{today}_{source_id}_{len(records)}.xlsx"
    df = pd.DataFrame(records)
    with pd.ExcelWriter(
        str(out_path),
        engine="xlsxwriter",
        engine_kwargs={"options": {"strings_to_urls": False}},
    ) as w:
        df.to_excel(w, sheet_name="Batch", index=False)

    exported_t = s.exported_table
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _pg_connect() as con:
        if exported_t:
            for (cid, fid) in all_exported_members:
                con.execute(
                    f"INSERT INTO {exported_t} "
                    f"(lead_id, founder_id, batch_file, exported_at) "
                    f"VALUES (?, ?, ?, ?) "
                    f"ON CONFLICT (lead_id, founder_id) DO NOTHING",
                    (cid, fid, out_path.name, now_iso),
                )
        exported_company_ids = {cid for (cid, _fid) in all_exported_members}
        if exported_company_ids:
            ph = ",".join("?" * len(exported_company_ids))
            con.execute(
                f"UPDATE {leads_t} SET needs_attention = 0 WHERE id IN ({ph})",
                tuple(exported_company_ids),
            )
        con.commit()

    return {
        "ok": True,
        "rows": len(records),
        "file": str(out_path),
        "file_name": out_path.name,
    }


def batch_status(path: Path) -> dict:
    """Read the xlsx and summarise progress state."""
    import pandas as pd
    try:
        df = pd.read_excel(path)
    except Exception as e:
        return {
            "total": 0, "drafted": 0, "in_outlook": 0, "sent": 0,
            "state": "fresh", "error": f"read failed: {e}",
        }
    total = len(df)

    def _filled(col: str) -> int:
        if col not in df.columns:
            return 0
        s = df[col].astype(str).str.strip().str.lower()
        return int(((s != "") & (s != "nan") & (s != "none")).sum())

    drafted = _filled("draft_subject")
    in_outlook = _filled("outlook_entry_id")
    sent = _filled("sent_at")

    if sent == total and total > 0:
        state = "sent"
    elif in_outlook == total and total > 0:
        state = "in_outlook"
    elif drafted == total and total > 0:
        state = "drafted"
    elif sent or in_outlook or drafted:
        state = "partial"
    else:
        state = "fresh"

    return {
        "total": total,
        "drafted": drafted,
        "in_outlook": in_outlook,
        "sent": sent,
        "state": state,
    }


def resolve_grab_batch(source_id: str, name: str) -> Path:
    get_source(source_id)
    d = settings.grab_batches_dir
    p = (d / name).resolve()
    if not str(p).startswith(str(d.resolve())) or not p.exists():
        raise HTTPException(404, f"Batch file not found: {name}")
    return p


def resolve_marcel_batch(file: str) -> str:
    """Resolve a Marcel batch filename safely under BATCHES_DIR."""
    p = (settings.batches_dir / file).resolve()
    if not p.exists() or not str(p).startswith(str(settings.batches_dir)):
        raise HTTPException(400, f"Batch file not found: {file}")
    return str(p)
