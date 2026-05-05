"""Pre-pipeline dependency check (DB + bridge + Outlook + account)."""
from __future__ import annotations

from app.config import settings
from app.db import q_one
from app.services.bridge import ping_bridge
from app.services.outlook import check_outlook


def preflight_report() -> dict:
    checks: list[dict] = []

    try:
        q_one("SELECT 1")
    except Exception as e:
        checks.append({"key": "db", "ok": False, "error": str(e)[:200]})
    else:
        checks.append({"key": "db", "ok": True})

    bridge_ok = ping_bridge(timeout=1.0)
    checks.append({
        "key": "bridge", "ok": bridge_ok,
        "error": None if bridge_ok else "Bridge not responding on :8766",
    })

    outlook_ok, account_ok, err = check_outlook()
    checks.append({
        "key": "outlook", "ok": outlook_ok,
        "error": None if outlook_ok else (err or "Outlook COM dispatch failed"),
    })
    checks.append({
        "key": "outlook_account",
        "ok": account_ok,
        "error": None if account_ok else f"{settings.outlook_account} not configured in Outlook Desktop",
    })

    return {"ok": all(c["ok"] for c in checks), "checks": checks}
