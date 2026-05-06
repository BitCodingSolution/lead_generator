"""LinkedIn — drafts routes.

Carved from `app.linkedin.api`. Routes are byte-identical to the
original; the wildcard import below inherits every helper and
module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.api import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


@router.post("/drafts/{lead_id}/generate")
def generate_draft(lead_id: int):
    with connect() as con:
        row = con.execute("SELECT * FROM ln_leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Lead not found")

    try:
        result = _claude_generate(
            posted_by=row["posted_by"] or "",
            company=row["company"] or "",
            role=row["role"] or "",
            tech_stack=row["tech_stack"] or "",
            location=row["location"] or "",
            post_text=row["post_text"] or "",
        )
    except BridgeUnreachable as e:
        # Bridge offline. Refuse to draft — a regex-only fallback would risk
        # archiving real leads. Leave the lead at its current status so the
        # user can retry after bringing the Bridge back up.
        raise HTTPException(
            503,
            f"Claude Bridge offline — cannot generate drafts without it. "
            f"Start the Bridge (Bridge online header button) and retry. "
            f"Detail: {e}",
        )
    except BridgeParseError as e:
        raise HTTPException(502, f"Bridge returned unparseable output — retry: {e}")
    except Exception as e:
        raise HTTPException(500, f"Draft generation failed: {e}")

    with connect() as con:
        # Auto-archive on Claude skip decision.
        if result.should_skip:
            con.execute(
                "UPDATE ln_leads SET gen_subject = ?, gen_body = ?, email_mode = ?, "
                "cv_cluster = ?, skip_reason = ?, skip_source = ?, "
                "status = 'Skipped' WHERE id = ?",
                (
                    result.subject, result.body, result.email_mode,
                    result.cv_cluster, result.skip_reason, result.skip_source,
                    lead_id,
                ),
            )
            _log_event(con, "draft_skipped", lead_id=lead_id,
                       meta={"reason": result.skip_reason})
            _archive_lead(con, lead_id, reason=f"auto_skip:{result.skip_reason}")
            con.commit()
            return {
                "status": "skipped",
                "skip_reason": result.skip_reason,
                "archived": True,
            }

        # If fallback returned no draft (Bridge down + no regex skip hit),
        # keep status=New so the next generate attempt re-runs cleanly.
        new_status = "Drafted" if (result.subject or result.body) else "New"
        con.execute(
            "UPDATE ln_leads SET gen_subject = ?, gen_body = ?, email_mode = ?, "
            "cv_cluster = ?, status = ?, skip_reason = NULL, "
            "skip_source = NULL WHERE id = ?",
            (
                result.subject, result.body, result.email_mode,
                result.cv_cluster, new_status, lead_id,
            ),
        )
        _log_event(con, "draft" if new_status == "Drafted" else "draft_fallback",
                   lead_id=lead_id,
                   meta={"mode": result.email_mode, "cv": result.cv_cluster})
        con.commit()

    return {
        "status": "drafted",
        "subject": result.subject,
        "body": result.body,
        "email_mode": result.email_mode,
        "cv_cluster": result.cv_cluster,
    }


@router.post("/drafts/generate/batch")
def generate_drafts_batch(payload: DraftBatchIn):
    with _drafts_lock:
        if _drafts_state["running"]:
            raise HTTPException(409, "A draft batch is already running")
        # Preflight Bridge health. Refusing at the door is far safer than
        # spawning a worker that would refuse every lead and look like the
        # batch "crashed". A single-shot probe (~1.5s) keeps the latency
        # unnoticeable when the Bridge IS up.
        if not bridge_is_up():
            raise HTTPException(
                503,
                "Claude Bridge offline — cannot start a draft batch. "
                "Click 'Bridge online' in the header to launch it, then retry.",
            )

        with connect() as con:
            rows = con.execute(
                "SELECT id FROM ln_leads "
                "WHERE status = 'New' "
                "  AND post_text IS NOT NULL AND TRIM(post_text) != '' "
                "ORDER BY first_seen_at ASC LIMIT ?",
                (payload.max,),
            ).fetchall()
            lead_ids = [r["id"] for r in rows]
            if not lead_ids:
                raise HTTPException(400, "No 'New' leads to draft")
            _log_event(con, "drafts_batch_start", meta={"count": len(lead_ids)})
            con.commit()

        _drafts_state.update({
            "running": True,
            "total": len(lead_ids),
            "drafted": 0,
            "skipped": 0,
            "failed": 0,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "last_error": None,
        })
        # Fresh batch → wipe the rolling variety window and seed the stats
        # snapshot once so every worker shares the same "avoid" hints.
        # Stats failure is silently ignored — empty hints just means the
        # drafter falls back to rule-only guidance.
        with _batch_context_lock:
            _batch_context["prior_drafts"] = []
            _batch_context["prior_plans"] = []
            try:
                _batch_context["stats"] = extras.outreach_stats()
            except Exception:
                _batch_context["stats"] = None
        threading.Thread(target=_drafts_worker, args=(lead_ids,), daemon=True).start()

    return {"started": True, "total": len(lead_ids)}


@router.get("/drafts/generate/status")
def drafts_batch_status():
    return dict(_drafts_state)
