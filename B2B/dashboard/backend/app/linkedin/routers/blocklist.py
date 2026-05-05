"""LinkedIn — blocklist routes.

Carved from `app.linkedin.extras`. Routes are byte-identical to
the original; the wildcard import below inherits every helper
and module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.extras import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin-extras"])


@router.get("/blocklist")
def list_blocklist():
    with connect() as con:
        rows = con.execute(
            "SELECT id, kind, value, reason, created_at "
            "FROM blocklist ORDER BY created_at DESC"
        ).fetchall()
        return {"rows": [dict(r) for r in rows]}


@router.post("/blocklist")
def add_blocklist(payload: BlocklistIn):
    value = payload.value.strip().lower()
    if payload.kind == "domain":
        value = value.lstrip("@")
        if not _DOMAIN_RE.match(value):
            raise HTTPException(400, "Domain must look like example.com")
    elif payload.kind == "email":
        if not _EMAIL_RE.match(value):
            raise HTTPException(400, "Must be a valid email address")

    with connect() as con:
        try:
            con.execute(
                "INSERT INTO blocklist (kind, value, reason, created_at) "
                "VALUES (?, ?, ?, ?)",
                (payload.kind, value, payload.reason, _now_iso()),
            )
        except Exception as e:
            if "UNIQUE" in str(e):
                raise HTTPException(409, f"{payload.kind} '{value}' already blocked")
            raise
        _log(con, "blocklist_add", meta=payload.model_dump() | {"value": value})
        archived = _archive_matching_leads(con, payload.kind, value,
                                           reason=payload.reason or "blocklist")
        con.commit()
    return {"ok": True, "archived_existing": archived}


@router.post("/blocklist/bulk")
def bulk_add_blocklist(payload: BlocklistBulkIn):
    """Paste a big list of emails / domains. Each non-empty token is
    inferred (contains '@' → email; else → domain) and inserted. Duplicates
    skipped silently. Existing matching leads are auto-archived to
    recyclebin so they drop out of the Drafted queue."""
    raw = payload.text.replace(",", "\n").replace(";", "\n")
    tokens = [t.strip().lower() for t in raw.splitlines() if t.strip()]

    added = {"email": 0, "domain": 0}
    skipped = 0
    invalid = []
    archived_total = 0

    with connect() as con:
        for tok in tokens:
            if "@" in tok:
                if not _EMAIL_RE.match(tok):
                    invalid.append(tok)
                    continue
                kind = "email"
            else:
                dom = tok.lstrip("@")
                if not _DOMAIN_RE.match(dom):
                    invalid.append(tok)
                    continue
                kind, tok = "domain", dom
            try:
                con.execute(
                    "INSERT INTO blocklist (kind, value, reason, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (kind, tok, payload.reason, _now_iso()),
                )
                added[kind] += 1
                archived_total += _archive_matching_leads(
                    con, kind, tok, reason=payload.reason or "blocklist_bulk"
                )
            except Exception as e:
                if "UNIQUE" in str(e):
                    skipped += 1
                else:
                    raise
        _log(con, "blocklist_bulk_add", meta={
            "added": added, "skipped_duplicates": skipped,
            "invalid": len(invalid), "archived_existing": archived_total,
        })
        con.commit()

    return {
        "ok": True,
        "added": added,
        "skipped_duplicates": skipped,
        "invalid": invalid[:20],   # first 20 for debugging
        "archived_existing": archived_total,
    }


@router.post("/blocklist/{item_id}/delete")
def del_blocklist(item_id: int):
    with connect() as con:
        cur = con.execute("DELETE FROM blocklist WHERE id = ?", (item_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Blocklist entry not found")
        _log(con, "blocklist_del", meta={"id": item_id})
        con.commit()
    return {"ok": True}
