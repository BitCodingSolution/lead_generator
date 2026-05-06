"""Outlook COM helpers (Windows-only)."""
from __future__ import annotations

from app.config import settings


def check_outlook() -> tuple[bool, bool, str | None]:
    """Return (outlook_running, account_present, error).

    Cheap COM dispatch; if Outlook isn't running it auto-starts. The
    account check is the real gate — without it write_to_outlook.py
    fails mid-pipeline.
    """
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application")
        accounts = [a.SmtpAddress.lower() for a in outlook.Session.Accounts]
        return True, settings.outlook_account.lower() in accounts, None
    except Exception as e:
        return False, False, str(e)[:200]
