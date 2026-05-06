"""LinkedIn — gmail routes.

Carved from `app.linkedin.api`. Routes are byte-identical to the
original; the wildcard import below inherits every helper and
module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.api import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


@router.get("/gmail/status")
def gmail_status():
    """Backward-compat single-account summary. Reflects the first active
    account so older UI keeps working. New UI uses /gmail/accounts."""
    accounts = gmail.list_accounts()
    active = [a for a in accounts if a["status"] == "active"]
    head = active[0] if active else (accounts[0] if accounts else None)
    if not head:
        return {"connected": False, "email": None, "connected_at": None,
                "total_accounts": 0, "active_accounts": 0}
    return {
        "connected": True,
        "email": head["email"],
        "connected_at": head["connected_at"],
        "last_verified_at": head["last_verified_at"],
        "total_accounts": len(accounts),
        "active_accounts": len(active),
    }


@router.get("/gmail/accounts")
def gmail_list_accounts():
    accounts = gmail.list_accounts()
    total_sent_today = sum(a["sent_today"] for a in accounts)
    total_cap = sum(a["daily_cap"] for a in accounts
                    if a["status"] == "active")
    return {
        "rows": accounts,
        "total_sent_today": total_sent_today,
        "total_daily_cap": total_cap,
    }


@router.post("/gmail/connect")
def gmail_connect(payload: GmailConnectIn):
    """Adds a new Gmail account OR updates the password on an existing one
    (matched by email). Runs SMTP+IMAP verification first."""
    try:
        check = gmail.verify_credentials(
            payload.email.strip(), payload.app_password.strip()
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    acc_id = gmail.save_credentials(
        payload.email.strip(), payload.app_password.strip(),
        display_name=(payload.display_name or "").strip() or None,
    )
    if payload.daily_cap is not None:
        gmail.set_account_cap(acc_id, payload.daily_cap)
    with connect() as con:
        _log_event(con, "gmail_connect",
                   meta={"email": payload.email, "account_id": acc_id, **check})
        con.commit()
    return {"connected": True, "email": payload.email,
            "account_id": acc_id, **check}


@router.post("/gmail/accounts/{account_id:int}/pause")
def gmail_pause_account(account_id: int):
    gmail.set_account_status(account_id, "paused")
    with connect() as con:
        _log_event(con, "gmail_pause", meta={"account_id": account_id})
        con.commit()
    return {"ok": True, "account_id": account_id, "status": "paused"}


@router.post("/gmail/accounts/{account_id:int}/resume")
def gmail_resume_account(account_id: int):
    gmail.set_account_status(account_id, "active")
    with connect() as con:
        _log_event(con, "gmail_resume", meta={"account_id": account_id})
        con.commit()
    return {"ok": True, "account_id": account_id, "status": "active"}


@router.post("/gmail/accounts/{account_id:int}/cap")
def gmail_set_cap(account_id: int, payload: GmailCapIn):
    gmail.set_account_cap(account_id, payload.daily_cap)
    return {"ok": True, "account_id": account_id, "daily_cap": payload.daily_cap}


@router.get("/gmail/warmup/curve")
def get_warmup_curve_ep():
    curve = gmail.get_warmup_curve()
    return {
        "stages": [[d, c] for d, c in curve],
        "default": [[d, c] for d, c in gmail.DEFAULT_WARMUP_CURVE],
    }


@router.post("/gmail/warmup/curve")
def set_warmup_curve_ep(payload: WarmupCurveIn):
    try:
        tuples = [(int(s[0]), int(s[1])) for s in payload.stages
                  if len(s) == 2]
    except Exception:
        raise HTTPException(400, "Each stage must be [days, cap] ints")
    if not tuples:
        raise HTTPException(400, "At least one stage required")
    try:
        gmail.save_warmup_curve(tuples)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with connect() as con:
        _log_event(con, "warmup_curve_update",
                   meta={"stages": [[d, c] for d, c in tuples]})
        con.commit()
    return {"ok": True, "stages": [[d, c] for d, c in tuples]}


@router.post("/gmail/accounts/{account_id:int}/warmup")
def gmail_set_warmup(account_id: int, payload: GmailWarmupIn):
    gmail.set_account_warmup(account_id, payload.enabled, payload.reset_start)
    with connect() as con:
        _log_event(con, "gmail_warmup",
                   meta={"account_id": account_id, "enabled": payload.enabled,
                         "reset_start": payload.reset_start})
        con.commit()
    return {"ok": True, "account_id": account_id,
            "warmup_enabled": payload.enabled}


@router.delete("/gmail/accounts/{account_id:int}")
def gmail_remove_account(account_id: int):
    gmail.remove_account(account_id)
    with connect() as con:
        _log_event(con, "gmail_remove", meta={"account_id": account_id})
        con.commit()
    return {"ok": True, "account_id": account_id}


@router.post("/gmail/test")
def gmail_test(account_id: Optional[int] = None):
    """Verify creds for a given account (or the first active if omitted)."""
    if account_id is not None:
        creds = gmail.get_account_creds(account_id)
        acc_id = account_id
    else:
        creds = gmail.get_credentials()
        accounts = gmail.list_accounts()
        acc_id = next((a["id"] for a in accounts if a["status"] == "active"), None)
    if not creds:
        raise HTTPException(400, "Gmail not connected")
    try:
        check = gmail.verify_credentials(*creds)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    now = dt.datetime.now().isoformat(timespec="seconds")
    if acc_id is not None:
        with connect() as con:
            con.execute(
                "UPDATE ln_gmail_accounts SET last_verified_at = ? WHERE id = ?",
                (now, acc_id),
            )
            con.commit()
    return {"ok": True, "account_id": acc_id, **check, "last_verified_at": now}
