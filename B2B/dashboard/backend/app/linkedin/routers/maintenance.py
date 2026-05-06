"""LinkedIn — maintenance routes.

Carved from `app.linkedin.extras`. Routes are byte-identical to
the original; the wildcard import below inherits every helper
and module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.extras import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin-extras"])


@router.post("/maintenance/reset-orphans")
def api_reset_orphans():
    return reset_orphans()


@router.post("/maintenance/sweep-junk")
def api_sweep_junk():
    """Bulk-archive clearly-junk leads:
      • No email + no phone + no draft + older than 7 days.
      • Skipped leads regardless of age.
      • Leads where Claude already wrote skip_reason but status is still 'New'.
    """
    seven_days_ago = (dt.datetime.now() - dt.timedelta(days=7)).isoformat(
        timespec="seconds",
    )
    archived = 0
    with connect() as con:
        # Collect candidates first so _archive_lead can operate per-row.
        rows = con.execute(
            "SELECT id FROM ln_leads WHERE ("
            "  (status = 'Skipped')"
            "  OR ((email IS NULL OR TRIM(email) = '') "
            "       AND (phone IS NULL OR TRIM(phone) = '') "
            "       AND (gen_subject IS NULL OR TRIM(gen_subject) = '') "
            "       AND last_seen_at < ?) "
            "  OR (skip_reason IS NOT NULL AND status = 'New')"
            ")",
            (seven_days_ago,),
        ).fetchall()
        for r in rows:
            _archive_lead_inline(con, r["id"], reason="swept_junk")
            archived += 1
        _log(con, "sweep_junk", meta={"count": archived})
        con.commit()
    return {"archived": archived}


@router.post("/recyclebin/clear")
def api_clear_recyclebin():
    """Free the recyclebin but remember which post_urls were rejected so
    they can't be re-ingested. Large payload_json rows go, a lightweight
    (post_url, reason) shadow row moves to archived_urls."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        moved = con.execute(
            "INSERT OR IGNORE INTO ln_archived_urls (post_url, reason, archived_at) "
            "SELECT post_url, reason, ? FROM ln_recyclebin "
            "WHERE post_url IS NOT NULL AND post_url != ''",
            (now,),
        ).rowcount
        cur = con.execute("DELETE FROM ln_recyclebin")
        deleted = cur.rowcount
        _log(con, "recyclebin_cleared", meta={"deleted": deleted, "shadowed": moved})
        con.commit()
    return {"deleted": deleted, "shadowed": moved}


@router.post("/recyclebin/purge")
def api_purge_recyclebin():
    """Fully forget. Deletes recyclebin AND archived_urls shadow rows, so
    previously-rejected posts can re-ingest as fresh leads."""
    with connect() as con:
        cur = con.execute("DELETE FROM ln_recyclebin")
        deleted = cur.rowcount
        cur2 = con.execute("DELETE FROM ln_archived_urls")
        forgotten = cur2.rowcount
        _log(con, "recyclebin_purged",
             meta={"deleted": deleted, "forgotten": forgotten})
        con.commit()
    return {"deleted": deleted, "forgotten": forgotten}


@router.post("/recyclebin/empty")
def api_empty_recyclebin():
    return api_clear_recyclebin()


@router.get("/recyclebin/export")
def export_recyclebin():
    cols_out = [
        "id", "original_id", "post_url", "reason", "moved_at",
        "company", "posted_by", "role", "email",
    ]
    with connect() as con:
        rows = con.execute(
            "SELECT id, original_id, post_url, reason, moved_at, payload_json "
            "FROM ln_recyclebin ORDER BY moved_at DESC"
        ).fetchall()

    data: list[list] = []
    for r in rows:
        p = json.loads(r["payload_json"] or "{}")
        data.append([
            r["id"], r["original_id"], r["post_url"], r["reason"], r["moved_at"],
            p.get("company"), p.get("posted_by"), p.get("role"), p.get("email"),
        ])
    return _csv_response(
        f"linkedin_recyclebin_{dt.date.today().isoformat()}.csv",
        cols_out,
        data,
    )
