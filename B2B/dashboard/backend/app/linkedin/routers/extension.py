"""LinkedIn — extension routes.

Carved from `app.linkedin.api`. Routes are byte-identical to the
original; the wildcard import below inherits every helper and
module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.api import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


@router.get("/extension/keys")
def list_extension_keys():
    with connect() as con:
        rows = con.execute(
            "SELECT key, label, created_at, last_used_at "
            "FROM extension_keys ORDER BY created_at DESC"
        ).fetchall()
        return {"rows": [dict(r) for r in rows]}


@router.post("/extension/keys")
def create_extension_key(payload: ExtensionKeyIn):
    key = f"li_{secrets.token_urlsafe(24)}"
    now = dt.datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        con.execute(
            "INSERT INTO extension_keys (key, label, created_at) VALUES (?, ?, ?)",
            (key, payload.label.strip(), now),
        )
        con.commit()
    return {"key": key, "label": payload.label.strip(), "created_at": now}


@router.post("/extension/keys/{key}/revoke")
def revoke_extension_key(key: str):
    with connect() as con:
        cur = con.execute("DELETE FROM extension_keys WHERE key = ?", (key,))
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Key not found")
    return {"revoked": key}


@router.post("/ingest")
def ingest(
    payload: IngestBatch,
    x_ext_key: Optional[str] = Header(default=None, alias="X-Ext-Key"),
):
    _require_ext_key(x_ext_key)
    inserted = 0
    updated = 0
    dup_bin = 0
    blocked = 0
    auto_skipped = 0
    missing_url = 0
    # Per-lead result so extension can map scan cards -> lead ids and
    # offer post-save actions (call_status toggle, etc.).
    items: list[dict] = []
    with connect() as con:
        for p in payload.leads:
            if not (p.post_url or "").strip():
                missing_url += 1
                items.append({"post_url": p.post_url, "action": "missing_url", "lead_id": None})
                continue
            lead_id, action = _upsert_lead(con, p)
            items.append({
                "post_url": p.post_url,
                "email": p.email,
                "action": action,
                "lead_id": lead_id if lead_id > 0 else None,
            })
            if action == "inserted":
                inserted += 1
                # Claude (from extension) already flagged this post as unfit
                # → auto-archive right after insert, same as server-side
                # draft flow does in /drafts/{id}/generate.
                if p.should_skip and lead_id > 0:
                    _archive_lead(con, lead_id,
                                  reason=f"auto_skip:{(p.skip_reason or 'claude').strip()}")
                    auto_skipped += 1
                elif lead_id > 0:
                    _rescore(con, lead_id)
            elif action == "updated":
                updated += 1
                if lead_id > 0:
                    _rescore(con, lead_id)
            elif action == "recyclebin_dup":
                dup_bin += 1
            elif action.startswith("blocked:"):
                blocked += 1
        _log_event(con, "ingest", meta={
            "inserted": inserted, "updated": updated,
            "dup_bin": dup_bin, "blocked": blocked,
            "auto_skipped": auto_skipped, "missing_url": missing_url,
        })
        con.commit()
    return {
        "inserted": inserted, "updated": updated,
        "dup_bin": dup_bin, "blocked": blocked,
        "auto_skipped": auto_skipped,
        "missing_url": missing_url,
        "total": len(payload.leads),
        "items": items,
    }


@router.post("/account-warning")
def account_warning(
    payload: AccountWarning,
    x_ext_key: Optional[str] = Header(default=None, alias="X-Ext-Key"),
):
    _require_ext_key(x_ext_key)
    if not WARNING_PHRASES_RE.search(payload.phrase or ""):
        raise HTTPException(400, "Phrase does not match any known warning signature")
    paused_until = (
        dt.datetime.now() + dt.timedelta(days=WARNING_PAUSE_DAYS)
    ).isoformat(timespec="seconds")
    with connect() as con:
        con.execute(
            "UPDATE safety_state SET warning_paused_until = ? WHERE id = 1",
            (paused_until,),
        )
        _log_event(con, "warning", meta={"phrase": payload.phrase, "url": payload.url})
        con.commit()
    return {"paused_until": paused_until}


@router.get("/t/open/{token}.gif")
def tracking_pixel(token: str, request: Request):
    """Public tracking beacon. Logs an open against the lead whose
    open_token matches. Always returns a 1x1 GIF — even on unknown tokens
    or any failure — so broken recipients never see a broken image."""
    try:
        ua = request.headers.get("user-agent", "")[:200]
        client = request.client.host if request.client else None
        with connect() as con:
            row = con.execute(
                "SELECT id FROM leads WHERE open_token = ?", (token,),
            ).fetchone()
            if row:
                now = dt.datetime.now().isoformat(timespec="seconds")
                con.execute(
                    "INSERT INTO email_opens (lead_id, opened_at, user_agent, ip) "
                    "VALUES (?, ?, ?, ?)",
                    (row["id"], now, ua, client),
                )
                con.execute(
                    "UPDATE leads SET open_count = COALESCE(open_count, 0) + 1, "
                    "first_opened_at = COALESCE(first_opened_at, ?), "
                    "last_opened_at = ? WHERE id = ?",
                    (now, now, row["id"]),
                )
                _log_event(con, "email_open", lead_id=row["id"],
                           meta={"ua": ua[:100]})
                con.commit()
    except Exception as e:
        print(f"[tracking] open log failed: {e}")
    return Response(
        content=_TRACKING_PIXEL_BYTES,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.get("/extension/download")
def download_extension():
    """Zip the linkedin_extension/ folder on the fly and stream as a
    download. Lets a user on any device grab the extension without
    needing Git or the Windows file path."""
    if not _EXT_DIR.is_dir():
        raise HTTPException(500, f"Extension folder not found at {_EXT_DIR}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in _EXT_DIR.rglob("*"):
            if fp.is_file():
                # Skip the local .zip itself, node_modules-style junk, and
                # OS-generated files.
                name = fp.name.lower()
                if name.endswith(".zip") or name in {".ds_store", "thumbs.db"}:
                    continue
                zf.write(fp, fp.relative_to(_EXT_DIR.parent))
    buf.seek(0)
    today = dt.date.today().isoformat()
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="linkedin_extension_{today}.zip"',
            "Cache-Control": "no-store",
        },
    )
