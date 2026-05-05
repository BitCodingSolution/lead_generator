"""LinkedIn — overview routes.

Carved from `app.linkedin.api`. Routes are byte-identical to the
original; the wildcard import below inherits every helper and
module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.api import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


@router.get("/overview", response_model=OverviewResponse)
def overview() -> OverviewResponse:
    with connect() as con:
        _roll_daily_counter(con)

        def cnt(where: str, params: tuple = ()) -> int:
            return con.execute(
                f"SELECT COUNT(*) FROM leads WHERE {where}", params
            ).fetchone()[0]

        total = con.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        new = cnt("status = 'New'")
        drafted = cnt("status = 'Drafted'")
        queued = cnt("status IN ('Queued', 'Sending')")
        sent_today = cnt("DATE(sent_at) = ?", (_today(),))
        replied = cnt("status = 'Replied'")
        # Pending = unhandled inbound replies. Mirror exactly what the
        # /replies UI shows in "Unhandled only" mode so the KPI counter
        # never disagrees with the inbox feed:
        #   - kind = 'reply' (excludes bounces / auto-replies)
        #   - handled_at IS NULL (still needs action)
        # Count rows (NOT distinct lead_id) — the inbox renders one row
        # per reply, so two unhandled mails from the same lead must show
        # as 2 in the badge.
        replied_pending = con.execute(
            "SELECT COUNT(*) FROM replies "
            "WHERE kind = 'reply' AND handled_at IS NULL"
        ).fetchone()[0]
        bounced = cnt("status = 'Bounced'")

        safety = con.execute("SELECT * FROM safety_state WHERE id=1").fetchone()
        gmail_row = con.execute(
            "SELECT 1 FROM gmail_accounts LIMIT 1"
        ).fetchone()
        auto_paused = con.execute(
            "SELECT id, email, paused_reason FROM gmail_accounts "
            "WHERE status = 'paused' AND paused_reason IS NOT NULL"
        ).fetchall()

        return OverviewResponse(
            total=total,
            new=new,
            drafted=drafted,
            queued=queued,
            sent_today=sent_today,
            replied=replied,
            replied_pending=replied_pending,
            bounced=bounced,
            quota_used=safety["daily_sent_count"] if safety else 0,
            quota_cap=_effective_daily_cap(con),
            gmail_connected=gmail_row is not None,
            autopilot_enabled=bool(safety["autopilot_enabled"]) if safety else False,
            safety_mode=safety["safety_mode"] if safety else "max",
            warning_paused_until=safety["warning_paused_until"] if safety else None,
            auto_paused_accounts=[
                AutoPausedAccount(id=r["id"], email=r["email"],
                                  reason=r["paused_reason"])
                for r in auto_paused
            ],
        )


@router.post("/safety")
def update_safety(patch: SafetyPatch):
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True}
    mode = updates.get("safety_mode")
    if mode is not None and mode not in ("max", "normal"):
        raise HTTPException(400, "safety_mode must be 'max' or 'normal'")

    db_updates: dict = {}
    if "safety_mode" in updates:
        db_updates["safety_mode"] = updates["safety_mode"]
    if "autopilot_enabled" in updates:
        db_updates["autopilot_enabled"] = 1 if updates["autopilot_enabled"] else 0
    if "autopilot_hour" in updates:
        db_updates["autopilot_hour"] = int(updates["autopilot_hour"])
    if "autopilot_minute" in updates:
        db_updates["autopilot_minute"] = int(updates["autopilot_minute"])
    if "autopilot_count" in updates:
        raw = updates["autopilot_count"]
        # -1 from the client is the "revert to full cap" sentinel.
        if raw is None or int(raw) <= 0:
            db_updates["autopilot_count"] = None
        else:
            db_updates["autopilot_count"] = int(raw)
    if "autopilot_tz" in updates:
        tz = (updates["autopilot_tz"] or "").strip()
        if tz:
            # Validate before persisting — zoneinfo raises on bad names.
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz)
            except Exception:
                raise HTTPException(400, f"Invalid IANA timezone: {tz}")
        db_updates["autopilot_tz"] = tz
    if "business_hours_only" in updates:
        db_updates["business_hours_only"] = 1 if updates["business_hours_only"] else 0
    if "followups_autopilot" in updates:
        db_updates["followups_autopilot"] = 1 if updates["followups_autopilot"] else 0
    if "followups_hour" in updates:
        db_updates["followups_hour"] = int(updates["followups_hour"])
    if updates.get("clear_warning_pause"):
        db_updates["warning_paused_until"] = None

    sets = ", ".join(f"{k} = ?" for k in db_updates)
    with connect() as con:
        con.execute(
            f"UPDATE safety_state SET {sets} WHERE id = 1",
            list(db_updates.values()),
        )
        _log_event(con, "safety_update", meta=updates)
        con.commit()
    return {"ok": True, "updated": list(db_updates.keys())}


@router.get("/safety", response_model=SafetyState)
def get_safety() -> SafetyState:
    with connect() as con:
        _roll_daily_counter(con)
        r = con.execute("SELECT * FROM safety_state WHERE id=1").fetchone()
        if r is None:
            raise HTTPException(500, "safety_state missing")
        today_iso = dt.date.today().isoformat()
        ap_row = con.execute(
            "SELECT fired_at, total_queued, status FROM autopilot_runs "
            "WHERE fired_date = ? ORDER BY id DESC LIMIT 1",
            (today_iso,),
        ).fetchone()
        today_run = (
            AutopilotTodayRun(
                fired_at=ap_row["fired_at"],
                total_queued=int(ap_row["total_queued"] or 0),
                status=ap_row["status"],
            )
            if ap_row
            else None
        )
        return SafetyState(
            daily_sent_count=r["daily_sent_count"],
            daily_sent_date=r["daily_sent_date"],
            last_send_at=r["last_send_at"],
            consecutive_failures=r["consecutive_failures"],
            warning_paused_until=r["warning_paused_until"],
            autopilot_enabled=bool(r["autopilot_enabled"]),
            autopilot_hour=r["autopilot_hour"],
            autopilot_minute=(
                r["autopilot_minute"] if "autopilot_minute" in r.keys() else 0
            ) or 0,
            autopilot_count=(
                r["autopilot_count"] if "autopilot_count" in r.keys() else None
            ),
            # Older DBs that pre-date the TZ column will still return None
            # via the keys(); default to empty string so pydantic doesn't
            # 500 until the schema catches up.
            autopilot_tz=(r["autopilot_tz"] if "autopilot_tz" in r.keys() else "") or "",
            business_hours_only=bool(
                r["business_hours_only"] if "business_hours_only" in r.keys() else 0
            ),
            safety_mode=r["safety_mode"],
            autopilot_today=today_run,
            followups_autopilot=bool(
                r["followups_autopilot"] if "followups_autopilot" in r.keys() else 0
            ),
            followups_hour=(
                r["followups_hour"] if "followups_hour" in r.keys() else 11
            ) or 11,
        )


@router.get("/runtime-settings")
def list_runtime_settings():
    """Return the descriptor + current value for each runtime-toggleable
    setting. The frontend uses descriptor.type to render the right input."""
    out = []
    for s in _RUNTIME_SETTINGS:
        if s["type"] == "bool":
            value = linkedin_db.get_setting_bool(
                s["key"], env_key=s.get("env_key"), default=s["default"],
            )
        elif s["type"] == "int":
            value = linkedin_db.get_setting_int(
                s["key"], env_key=s.get("env_key"), default=s["default"],
            )
        else:
            value = linkedin_db.get_setting_raw(s["key"]) or s["default"]
        out.append({**s, "value": value})
    return {"settings": out}


@router.post("/runtime-settings")
def update_runtime_setting(payload: RuntimeSettingUpdate):
    """Set one runtime setting. Rejects unknown keys so a typo can't
    silently bloat the kv_settings table with junk."""
    desc = next((s for s in _RUNTIME_SETTINGS if s["key"] == payload.key), None)
    if desc is None:
        raise HTTPException(400, f"Unknown setting key: {payload.key}")
    if desc["type"] == "bool":
        as_str = "true" if bool(payload.value) else "false"
    elif desc["type"] == "int":
        try:
            as_str = str(int(payload.value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise HTTPException(400, f"value for {payload.key} must be int")
    else:
        as_str = str(payload.value)
    linkedin_db.set_setting_raw(payload.key, as_str)
    with connect() as con:
        _log_event(con, "runtime_setting", meta={"key": payload.key, "value": as_str})
        con.commit()
    return {"ok": True, "key": payload.key, "value": as_str}


@router.post("/autopilot/reset-today")
def reset_autopilot_today():
    """Clear today's autopilot run so the next tick can re-fire the daily
    batch. Useful when the user changes the scheduled time AFTER the run
    already happened, or wants to manually re-trigger for the same day."""
    today_iso = dt.date.today().isoformat()
    with connect() as con:
        cur = con.execute(
            "DELETE FROM autopilot_runs WHERE fired_date = ?", (today_iso,)
        )
        deleted = cur.rowcount
        _log_event(con, "autopilot_reset", meta={"deleted": deleted})
        con.commit()
    # Also clear the in-process guard so _autopilot_tick re-evaluates.
    _autopilot_state["last_fired_date"] = None
    return {"ok": True, "deleted": deleted}
