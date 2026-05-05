"""
Gmail SMTP + IMAP helper for the LinkedIn module (multi-account).

No Google Cloud / OAuth. The user generates an App Password at
https://myaccount.google.com/apppasswords and pastes it into
/linkedin/settings. We encrypt it at rest with Fernet and decrypt in-memory
only when sending / polling.

Public surface:
    list_accounts()                  -> [dict, ...]
    save_credentials(email, pw, name)-> int   (insert or update, returns id)
    remove_account(id)
    set_account_status(id, active|paused)
    set_account_cap(id, daily_cap)
    set_account_warmup(id, enabled, reset_start=False)
    get_account_creds(id)            -> (email, app_password) | None
    get_credentials()                -> (email, app_password) | None    # first active
    pick_next_account_id()           -> int | None   (round-robin picker)
    verify_credentials(email, pw)    -> dict (live SMTP + IMAP login)
    send_email(..., account_id=None) -> SendResult
    poll_recent(account_id, since_uid) -> (list[InboxMsg], new_uid)
    record_send_failure(id, err)     -> bool  (True if just auto-paused)
    record_bounce(id)                -> bool  (True if just auto-paused)
    reconcile_today_counts()         -> dict
    get_warmup_curve() / save_warmup_curve(curve)
    effective_cap(cap, enabled, start, curve=None) -> int
"""
from __future__ import annotations

import datetime as dt
import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.linkedin.db import DB_PATH, connect

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

_KEY_FILE = DB_PATH.parent / ".fernet.key"


# --- encryption ------------------------------------------------------------


def _load_key() -> bytes:
    """Load or create the Fernet key. Lives next to leads.db, OS-protected by
    the Database folder's permissions. Regenerating the key will invalidate
    the stored password (user re-enters)."""
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    return key


def _fernet() -> Fernet:
    return Fernet(_load_key())


def _encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def _decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError(
            "Gmail credential decrypt failed — key file rotated. Re-connect Gmail."
        ) from e


# --- credential store (multi-account) --------------------------------------


def _today() -> str:
    return dt.date.today().isoformat()


# Default warmup curve — stepwise ramp from a cold account to full cap
# over ~14 days. Kept conservative because Gmail's spam signals are
# particularly sensitive during an inbox's first week of outbound.
# User-configurable via safety_state.warmup_curve_json (list of [days, cap]
# pairs, sorted ascending by days). Users can disable per-account with
# warmup_enabled=0 if they're importing an already-warm inbox.
DEFAULT_WARMUP_CURVE: list[tuple[int, int]] = [
    (1, 5),    # day 0  (first day) → 5
    (3, 10),   # days 1-2 → 10
    (7, 20),   # days 3-6 → 20
    (14, 35),  # days 7-13 → 35
]


def get_warmup_curve() -> list[tuple[int, int]]:
    """Load curve from safety_state, falling back to the default. Returns
    a list of (threshold_days, cap) pairs sorted by threshold_days."""
    try:
        with connect() as con:
            r = con.execute(
                "SELECT warmup_curve_json FROM safety_state WHERE id = 1"
            ).fetchone()
        raw = r["warmup_curve_json"] if r else None
        if not raw:
            return list(DEFAULT_WARMUP_CURVE)
        parsed = json.loads(raw)
        curve = [(int(d), int(c)) for d, c in parsed
                 if int(d) > 0 and int(c) > 0]
        curve.sort(key=lambda p: p[0])
        return curve or list(DEFAULT_WARMUP_CURVE)
    except Exception:
        return list(DEFAULT_WARMUP_CURVE)


def save_warmup_curve(curve: list[tuple[int, int]]) -> None:
    """Persist a new curve. Caller should validate non-empty + sorted."""
    normalized = [[int(d), int(c)] for d, c in curve
                  if int(d) > 0 and int(c) > 0]
    normalized.sort(key=lambda p: p[0])
    if not normalized:
        raise ValueError("warmup curve cannot be empty")
    with connect() as con:
        con.execute(
            "UPDATE safety_state SET warmup_curve_json = ? WHERE id = 1",
            (json.dumps(normalized),),
        )
        con.commit()


def _days_since(iso_date: Optional[str]) -> int:
    if not iso_date:
        return 14     # no start date → treat as fully warm
    try:
        d = dt.date.fromisoformat(iso_date[:10])
    except Exception:
        return 14
    return max(0, (dt.date.today() - d).days)


def effective_cap(daily_cap: int, warmup_enabled: bool,
                  warmup_start_date: Optional[str],
                  curve: Optional[list[tuple[int, int]]] = None) -> int:
    """The cap actually enforced by the picker. Min(daily_cap, curve stage).
    Pass `curve` to avoid a DB read when computing many caps in a row (e.g.
    list_accounts); otherwise the current saved curve is loaded."""
    if not warmup_enabled:
        return daily_cap
    if curve is None:
        curve = get_warmup_curve()
    days = _days_since(warmup_start_date)
    for threshold_days, cap in curve:
        if days < threshold_days:
            return min(daily_cap, cap)
    return daily_cap


def _roll_if_stale_day(con, today: str) -> None:
    """Lazy midnight rollover for per-day counters. Any row where
    sent_date != today has both sent_today AND bounce_count_today zeroed
    in one pass. Idempotent — safe to call on every read."""
    con.execute(
        "UPDATE gmail_accounts SET sent_today = 0, bounce_count_today = 0, "
        "sent_date = ? WHERE sent_date IS NULL OR sent_date != ?",
        (today, today),
    )


def list_accounts() -> list[dict]:
    """Return all accounts with per-day counters auto-rolled + reconciled.
    Reconciliation is cheap (one COUNT per account) and guarantees the UI
    reflects actual leads.sent_at truth — important when legacy sends,
    backfills, or mid-day restarts could otherwise leave the counter stale.

    Order matters: rollover happens FIRST (so a stale day zeros
    bounce_count_today + sent_today), THEN reconcile re-sets sent_today
    from today's lead rows. Bounces stay at 0 on a fresh day unless a new
    bounce fires."""
    today = _today()
    with connect() as con:
        _roll_if_stale_day(con, today)
        con.commit()
    try:
        reconcile_today_counts()
    except Exception:
        # Never let a reconcile failure take down the UI read.
        pass
    with connect() as con:
        rows = con.execute(
            "SELECT id, email, display_name, daily_cap, sent_today, "
            "sent_date, last_sent_at, status, warmup_enabled, "
            "warmup_start_date, consecutive_failures, bounce_count_today, "
            "paused_reason, connected_at, last_verified_at "
            "FROM gmail_accounts ORDER BY id ASC"
        ).fetchall()
    curve = get_warmup_curve()
    out = []
    with connect() as con:
        # Per-account health inputs. 30-day window keeps the signal fresh
        # without a brand-new account looking permanently bad after one
        # early bounce. Reply count is informational — we don't penalise
        # for low replies because that mostly reflects deliverability,
        # not account health.
        health_rows = con.execute(
            "SELECT sent_via_account_id AS aid, "
            "       COUNT(*) AS sent30, "
            "       SUM(CASE WHEN replied_at IS NOT NULL THEN 1 ELSE 0 END) AS replied30, "
            "       SUM(CASE WHEN bounced_at IS NOT NULL THEN 1 ELSE 0 END) AS bounced30 "
            "FROM leads "
            "WHERE sent_via_account_id IS NOT NULL "
            "  AND sent_at IS NOT NULL "
            "  AND DATE(sent_at) >= DATE('now', '-30 day') "
            "GROUP BY sent_via_account_id"
        ).fetchall()
        health_by_id = {int(h["aid"]): dict(h) for h in health_rows}
    for r in rows:
        d = dict(r)
        start = d.get("warmup_start_date") or d.get("connected_at")
        d["warmup_day"] = _days_since(start)
        d["effective_cap"] = effective_cap(
            int(d["daily_cap"]),
            bool(d["warmup_enabled"]),
            start,
            curve=curve,
        )
        # Health score: 100 minus deductions for bounce rate, consecutive
        # failures, today's bounces, and paused state. Cheap to compute
        # and easy to reason about — a single number the UI colour-codes.
        h = health_by_id.get(int(d["id"]), {})
        sent30 = int(h.get("sent30", 0) or 0)
        bounced30 = int(h.get("bounced30", 0) or 0)
        bounce_rate = (bounced30 / sent30) if sent30 else 0.0
        score = 100
        if sent30 >= 5:
            # 1% bounce rate == 3 points; 5% == 15. Only apply once we
            # have enough volume to trust the rate.
            score -= int(min(60, bounce_rate * 300))
        score -= int(min(20, int(d.get("consecutive_failures") or 0) * 5))
        score -= int(min(20, int(d.get("bounce_count_today") or 0) * 10))
        if d.get("status") == "paused":
            score -= 30
        score = max(0, min(100, score))
        d["health_score"] = score
        d["health_30d"] = {
            "sent": sent30,
            "replied": int(h.get("replied30", 0) or 0),
            "bounced": bounced30,
            "bounce_rate_pct": round(bounce_rate * 100, 1),
        }
        out.append(d)
    return out


def save_credentials(email_addr: str, app_password: str,
                     display_name: Optional[str] = None) -> int:
    """Insert a new account OR update an existing row keyed by email.
    Returns the account id."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    enc = _encrypt(app_password)
    with connect() as con:
        row = con.execute(
            "SELECT id FROM gmail_accounts WHERE email = ?", (email_addr,)
        ).fetchone()
        if row:
            acc_id = row["id"]
            con.execute(
                "UPDATE gmail_accounts SET app_password_enc = ?, "
                "display_name = COALESCE(?, display_name), "
                "last_verified_at = ?, status = 'active' WHERE id = ?",
                (enc, display_name, now, acc_id),
            )
        else:
            cur = con.execute(
                "INSERT INTO gmail_accounts (email, app_password_enc, "
                "display_name, connected_at, last_verified_at, sent_date, "
                "warmup_start_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (email_addr, enc, display_name, now, now, _today(), _today()),
            )
            acc_id = cur.lastrowid
        con.commit()
    return acc_id


def remove_account(account_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM gmail_accounts WHERE id = ?", (account_id,))
        con.commit()


def set_account_status(account_id: int, status: str) -> None:
    if status not in ("active", "paused"):
        raise ValueError(f"bad status: {status}")
    with connect() as con:
        if status == "active":
            # Manual resume: clear BOTH auto-pause rails (failure counter +
            # today's bounce counter) so the account gets a genuinely clean
            # slate. If bounces keep coming they'll re-trip in the usual way.
            con.execute(
                "UPDATE gmail_accounts SET status = 'active', "
                "paused_reason = NULL, consecutive_failures = 0, "
                "bounce_count_today = 0 WHERE id = ?",
                (account_id,),
            )
        else:
            con.execute(
                "UPDATE gmail_accounts SET status = 'paused' WHERE id = ?",
                (account_id,),
            )
        con.commit()


def set_account_cap(account_id: int, daily_cap: int) -> None:
    with connect() as con:
        con.execute(
            "UPDATE gmail_accounts SET daily_cap = ? WHERE id = ?",
            (max(1, int(daily_cap)), account_id),
        )
        con.commit()


def set_account_warmup(account_id: int, enabled: bool,
                       reset_start: bool = False) -> None:
    """Toggle warmup enforcement. If reset_start, sets warmup_start_date to
    today — useful for 'start over' on an account that got paused."""
    with connect() as con:
        if reset_start:
            con.execute(
                "UPDATE gmail_accounts SET warmup_enabled = ?, "
                "warmup_start_date = ? WHERE id = ?",
                (1 if enabled else 0, _today(), account_id),
            )
        else:
            con.execute(
                "UPDATE gmail_accounts SET warmup_enabled = ? WHERE id = ?",
                (1 if enabled else 0, account_id),
            )
        con.commit()


def get_account_creds(account_id: int) -> Optional[tuple[str, str]]:
    with connect() as con:
        r = con.execute(
            "SELECT email, app_password_enc FROM gmail_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
    if not r:
        return None
    return r["email"], _decrypt(r["app_password_enc"])


# Per-account cooldown: after any account sends, it can't send again for
# MIN_ACCOUNT_GAP_S seconds. Protects the edge case where one account
# carries the whole load (e.g. its sibling is paused) — without this rail
# the global 60-90s batch jitter becomes the *per-account* cadence, which
# looks bot-like at scale.
MIN_ACCOUNT_GAP_S = int(os.environ.get("DASHBOARD_MIN_ACCT_GAP_S", "300"))


def _cooldown_remaining_s(last_sent_at: Optional[str], now: dt.datetime) -> int:
    """Seconds this account still has to wait before its next send. 0 if
    clear. Invalid timestamps are treated as 0 (fail open)."""
    if not last_sent_at:
        return 0
    try:
        last = dt.datetime.fromisoformat(last_sent_at)
    except Exception:
        return 0
    elapsed = (now - last).total_seconds()
    return max(0, int(MIN_ACCOUNT_GAP_S - elapsed))


def pick_next_account_id() -> Optional[int]:
    """Pick an active account with remaining effective (warmup-aware) quota
    AND past its per-account cooldown. Round-robins by oldest last_sent_at
    so accounts alternate evenly. Returns None if no account can send *right
    now* — caller should use seconds_until_next_account() to tell idle-cooldown
    apart from truly-exhausted quota."""
    today = _today()
    now = dt.datetime.now()
    with connect() as con:
        _roll_if_stale_day(con, today)
        con.commit()
        # Ordering:
        # 1. sent_today ASC — load-balance across accounts (whoever's
        #    behind today gets the next send).
        # 2. last_sent_at ASC — if two accounts are tied on count, pick
        #    the one that hasn't sent most recently (gives each account
        #    space between sends).
        # 3. id ASC — stable fallback.
        # NOTE: sent_today is pulled raw here; if sent_date is stale we
        # still treat it as 0 when computing effective usage below, so
        # accounts that haven't reset yet don't get de-prioritized.
        rows = con.execute(
            "SELECT id, sent_today, sent_date, daily_cap, last_sent_at, "
            "       warmup_enabled, warmup_start_date, connected_at "
            "FROM gmail_accounts WHERE status = 'active' "
            "ORDER BY sent_today ASC, COALESCE(last_sent_at, '') ASC, id ASC"
        ).fetchall()
        curve = get_warmup_curve()
        for r in rows:
            cur = r["sent_today"] if r["sent_date"] == today else 0
            cap = effective_cap(
                int(r["daily_cap"]),
                bool(r["warmup_enabled"]),
                r["warmup_start_date"] or r["connected_at"],
                curve=curve,
            )
            if cur >= cap:
                continue
            if _cooldown_remaining_s(r["last_sent_at"], now) > 0:
                continue
            return r["id"]
    return None


def seconds_until_next_account() -> Optional[int]:
    """Non-zero seconds to wait if every eligible account is only in
    cooldown (has quota remaining but sent too recently). Returns:
      - 0 if at least one account is ready now
      - int > 0 if some account will be ready after waiting
      - None if ALL accounts are out of quota for today (the caller should
        stop, not wait)
    """
    today = _today()
    now = dt.datetime.now()
    min_wait: Optional[int] = None
    any_has_quota = False
    with connect() as con:
        rows = con.execute(
            "SELECT sent_today, sent_date, daily_cap, last_sent_at, "
            "       warmup_enabled, warmup_start_date, connected_at "
            "FROM gmail_accounts WHERE status = 'active'"
        ).fetchall()
        curve = get_warmup_curve()
    for r in rows:
        cur = r["sent_today"] if r["sent_date"] == today else 0
        cap = effective_cap(
            int(r["daily_cap"]),
            bool(r["warmup_enabled"]),
            r["warmup_start_date"] or r["connected_at"],
            curve=curve,
        )
        if cur >= cap:
            continue
        any_has_quota = True
        wait = _cooldown_remaining_s(r["last_sent_at"], now)
        if wait == 0:
            return 0
        if min_wait is None or wait < min_wait:
            min_wait = wait
    if not any_has_quota:
        return None  # nobody has quota — truly exhausted
    return min_wait or 0


def reconcile_today_counts() -> dict:
    """Recount gmail_accounts.sent_today from today's leads.sent_at rows.
    Also backfills leads.sent_via_account_id = 1 for legacy rows sent before
    multi-account landed (only when exactly one account exists, so the
    attribution is unambiguous). Safe to call on every boot."""
    today = _today()
    with connect() as con:
        accounts = con.execute(
            "SELECT id FROM gmail_accounts ORDER BY id ASC"
        ).fetchall()
        if not accounts:
            return {"accounts": 0, "backfilled": 0}

        backfilled = 0
        if len(accounts) == 1:
            only_id = accounts[0]["id"]
            cur = con.execute(
                "UPDATE leads SET sent_via_account_id = ? "
                "WHERE sent_via_account_id IS NULL AND sent_at IS NOT NULL",
                (only_id,),
            )
            backfilled = cur.rowcount or 0

        for a in accounts:
            count = con.execute(
                "SELECT COUNT(*) AS n FROM leads "
                "WHERE sent_via_account_id = ? "
                "  AND sent_at IS NOT NULL "
                "  AND substr(sent_at, 1, 10) = ?",
                (a["id"], today),
            ).fetchone()["n"]
            latest = con.execute(
                "SELECT MAX(sent_at) AS mx FROM leads "
                "WHERE sent_via_account_id = ? AND sent_at IS NOT NULL",
                (a["id"],),
            ).fetchone()["mx"]
            con.execute(
                "UPDATE gmail_accounts SET sent_today = ?, sent_date = ?, "
                "last_sent_at = COALESCE(?, last_sent_at) WHERE id = ?",
                (count, today, latest, a["id"]),
            )
        con.commit()
    return {"accounts": len(accounts), "backfilled": backfilled}


# Auto-pause trips if an account accumulates this many consecutive SMTP
# errors OR this many bounces in a single day. Both are deliverability
# emergencies — better to pause for review than keep burning sender rep.
AUTO_PAUSE_FAILURE_THRESHOLD = 3
AUTO_PAUSE_BOUNCE_THRESHOLD = 3


def _record_send(account_id: int, sent_at: str) -> None:
    with connect() as con:
        cur = con.execute(
            "SELECT sent_date FROM gmail_accounts WHERE id = ?", (account_id,),
        ).fetchone()
        if cur and cur["sent_date"] != _today():
            con.execute(
                "UPDATE gmail_accounts SET sent_today = 1, sent_date = ?, "
                "last_sent_at = ?, consecutive_failures = 0, "
                "bounce_count_today = 0 WHERE id = ?",
                (_today(), sent_at, account_id),
            )
        else:
            con.execute(
                "UPDATE gmail_accounts SET sent_today = sent_today + 1, "
                "last_sent_at = ?, consecutive_failures = 0 WHERE id = ?",
                (sent_at, account_id),
            )
        con.commit()


def record_send_failure(account_id: int, err: str) -> bool:
    """Increment SMTP failure counter; auto-pause at threshold. Returns
    True if the account was just paused by this call."""
    now_paused = False
    reason = f"SMTP failure: {err[:140]}"
    with connect() as con:
        con.execute(
            "UPDATE gmail_accounts SET consecutive_failures = "
            "COALESCE(consecutive_failures, 0) + 1 WHERE id = ?",
            (account_id,),
        )
        row = con.execute(
            "SELECT consecutive_failures, status FROM gmail_accounts "
            "WHERE id = ?", (account_id,),
        ).fetchone()
        if row and row["status"] == "active" and \
           (row["consecutive_failures"] or 0) >= AUTO_PAUSE_FAILURE_THRESHOLD:
            con.execute(
                "UPDATE gmail_accounts SET status = 'paused', "
                "paused_reason = ? WHERE id = ?",
                (reason, account_id),
            )
            now_paused = True
        con.commit()
    return now_paused


def record_bounce(account_id: int) -> bool:
    """Increment today's bounce counter; auto-pause at threshold. Returns
    True if the account was just paused by this call. Rolls over cleanly
    across midnight via the shared _roll_if_stale_day helper."""
    now_paused = False
    today = _today()
    with connect() as con:
        _roll_if_stale_day(con, today)
        cur = con.execute(
            "SELECT bounce_count_today, status FROM gmail_accounts "
            "WHERE id = ?", (account_id,),
        ).fetchone()
        if not cur:
            return False
        con.execute(
            "UPDATE gmail_accounts SET bounce_count_today = "
            "COALESCE(bounce_count_today, 0) + 1 WHERE id = ?",
            (account_id,),
        )
        new_count = (cur["bounce_count_today"] or 0) + 1
        if cur["status"] == "active" and new_count >= AUTO_PAUSE_BOUNCE_THRESHOLD:
            con.execute(
                "UPDATE gmail_accounts SET status = 'paused', "
                "paused_reason = ? WHERE id = ?",
                (f"{new_count} bounces today", account_id),
            )
            now_paused = True
        con.commit()
    return now_paused


# --- backward-compat shims (single-account callers) ------------------------


def get_credentials() -> Optional[tuple[str, str]]:
    """Legacy single-account getter. Returns the lowest-id active account's
    creds if any. Kept so old call sites keep working; new code should call
    get_account_creds(id)."""
    with connect() as con:
        r = con.execute(
            "SELECT id, email, app_password_enc FROM gmail_accounts "
            "WHERE status = 'active' ORDER BY id ASC LIMIT 1"
        ).fetchone()
    if not r:
        return None
    return r["email"], _decrypt(r["app_password_enc"])




# --- verification (live SMTP + IMAP login) --------------------------------


def verify_credentials(email_addr: str, app_password: str) -> dict:
    """Runs a real LOGIN against SMTP and IMAP. Returns {smtp_ok, imap_ok}.
    Raises on total failure so the caller can surface the message."""
    out = {"smtp_ok": False, "imap_ok": False}
    ctx = ssl.create_default_context()

    # SMTP
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20) as s:
            s.login(email_addr, app_password)
            out["smtp_ok"] = True
    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError(
            "SMTP authentication failed. App Password wrong or 2FA not enabled."
        ) from e
    except Exception as e:
        raise RuntimeError(f"SMTP connection failed: {e}") from e

    # IMAP (non-fatal — SMTP is the must-have for send)
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=20)
        m.login(email_addr, app_password)
        m.logout()
        out["imap_ok"] = True
    except Exception:
        pass

    return out


# --- send ------------------------------------------------------------------


@dataclass
class SendResult:
    message_id: str
    sent_at: str
    account_id: Optional[int] = None


@dataclass
class InboxMsg:
    uid: int
    message_id: str
    in_reply_to: str       # upstream Message-ID this is replying to
    references: str        # full References header (space-separated IDs)
    from_email: str
    subject: str
    snippet: str           # first 500 chars of body
    body: str              # full parsed plain-text body
    received_at: str
    kind: str              # reply | bounce | auto_reply | unknown


def _text_to_html(body: str) -> str:
    """Convert plain-text cold-email body to minimal HTML (line breaks only,
    no restyling — we want the email to read like a plain message)."""
    from html import escape
    escaped = escape(body).replace("\r\n", "\n")
    paragraphs = escaped.split("\n\n")
    html_paras = "".join(
        f"<p style=\"margin:0 0 1em 0\">{p.replace(chr(10), '<br>')}</p>"
        for p in paragraphs if p.strip()
    )
    return (
        "<!doctype html><html><body style=\"font-family:Arial,Helvetica,"
        "sans-serif;font-size:14px;line-height:1.5;color:#111\">"
        f"{html_paras}"
        "</body></html>"
    )


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    reply_to: Optional[str] = None,
    attachment: Optional[tuple[Path, str]] = None,
    account_id: Optional[int] = None,
    tracking_pixel_url: Optional[str] = None,
    in_reply_to: Optional[str] = None,     # Gmail Message-ID of the mail we're replying to
    references: Optional[str] = None,      # Full References chain for threading
) -> SendResult:
    """Send a cold-email body as multipart/alternative (plain + HTML) so a
    tracking pixel can ride along in the HTML part. Plain-text clients still
    get a readable message. If `attachment` is provided, the whole thing is
    wrapped in multipart/mixed so the PDF sits alongside the alternative.

    If `account_id` is omitted, picks the next active account with remaining
    daily quota (round-robin). Raises if no account available."""
    if account_id is None:
        account_id = pick_next_account_id()
        if account_id is None:
            raise RuntimeError("No Gmail account with remaining quota")
    creds = get_account_creds(account_id)
    if not creds:
        raise RuntimeError(f"Gmail account {account_id} not found")
    email_addr, app_password = creds

    bracketed = email.utils.make_msgid(domain="bitcoding.local")
    msg_id = bracketed.strip("<>").strip()

    # Build the alternative (plain + html) body.
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain", "utf-8"))
    html = _text_to_html(body)
    if tracking_pixel_url:
        # Invisible 1x1 at end of body. Zero-width styling so it never takes
        # visible space. display:none would cause some clients to skip-load.
        html = html.replace(
            "</body>",
            f'<img src="{tracking_pixel_url}" width="1" height="1" '
            f'style="display:block;border:0;width:1px;height:1px" '
            f'alt="" /></body>',
        )
    alt.attach(MIMEText(html, "html", "utf-8"))

    if attachment:
        msg = MIMEMultipart("mixed")
        msg.attach(alt)
        path, filename = attachment
        try:
            data = path.read_bytes()
        except Exception as e:
            raise RuntimeError(f"Could not read attachment {path}: {e}") from e
        part = MIMEBase("application", "pdf")
        part.set_payload(data)
        encoders.encode_base64(part)
        safe_name = filename if filename.lower().endswith(".pdf") else f"{filename}.pdf"
        part.add_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        msg.attach(part)
    else:
        msg = alt

    msg["From"] = email_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = bracketed
    if reply_to:
        msg["Reply-To"] = reply_to
    # Thread-preserving headers — without these Gmail shows the reply as a
    # new conversation instead of nesting under the original thread.
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to if in_reply_to.startswith("<") else f"<{in_reply_to}>"
    if references:
        msg["References"] = references

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as s:
        s.login(email_addr, app_password)
        s.sendmail(email_addr, [to], msg.as_string())

    sent_at = dt.datetime.now().isoformat(timespec="seconds")
    _record_send(account_id, sent_at)
    return SendResult(
        message_id=msg_id,
        sent_at=sent_at,
        account_id=account_id,
    )


# --- IMAP poll -------------------------------------------------------------


_BOUNCE_SENDERS_RE = re.compile(
    r"(mailer-daemon|postmaster|mail-delivery|mail\.delivery|bounce)@",
    re.IGNORECASE,
)
_AUTOREPLY_SUBJECT_RE = re.compile(
    r"\b(out of office|auto[\s-]?reply|on vacation|away from the office|"
    r"automatic reply)\b",
    re.IGNORECASE,
)


def _classify(from_email: str, subject: str, headers: dict) -> str:
    # Order matters: NDRs (mailer-daemon / postmaster) almost always carry
    # an "Auto-Submitted: auto-replied" header too. Check the bounce sender
    # FIRST so we don't misclassify hard bounces as plain auto-replies.
    if _BOUNCE_SENDERS_RE.search(from_email or ""):
        return "bounce"
    if "X-Failed-Recipients" in headers:
        return "bounce"
    if "auto-submitted" in {k.lower() for k in headers}:
        auto = str(headers.get("Auto-Submitted", "")).lower()
        if "auto-replied" in auto or "auto-generated" in auto:
            return "auto_reply"
    if "X-Autoreply" in headers:
        return "auto_reply"
    if _AUTOREPLY_SUBJECT_RE.search(subject or ""):
        return "auto_reply"
    return "reply"


def _extract_addr(raw: str) -> str:
    if not raw:
        return ""
    _name, addr = email.utils.parseaddr(raw)
    return (addr or raw).strip().strip("<>")


def _extract_msgid(raw: str) -> str:
    if not raw:
        return ""
    return raw.strip().strip("<>").strip()


def _fetch_message(m: imaplib.IMAP4_SSL, uid: bytes) -> Optional[InboxMsg]:
    typ, data = m.uid("fetch", uid, "(BODY.PEEK[HEADER] BODY.PEEK[TEXT])")
    if typ != "OK" or not data:
        return None
    # pyimap returns nested tuples per requested BODY section, in whatever
    # order the server chose. Dispatch by the label (`BODY[HEADER]` vs
    # `BODY[TEXT]`) in element [0] rather than assuming positional order —
    # Gmail sometimes returns TEXT first, which left all headers empty and
    # every message classified as an unmatchable reply.
    header_raw = b""
    body_raw = b""
    for p in data:
        if not isinstance(p, tuple) or len(p) < 2:
            continue
        label = p[0] if isinstance(p[0], (bytes, bytearray)) else b""
        payload = p[1] if isinstance(p[1], (bytes, bytearray)) else b""
        if b"BODY[HEADER]" in label:
            header_raw = payload
        elif b"BODY[TEXT]" in label:
            body_raw = payload
    if not header_raw:
        return None
    try:
        msg = email.message_from_bytes(header_raw)
    except Exception:
        return None
    raw_subj = msg.get("Subject", "") or ""
    try:
        subject = str(email.header.make_header(email.header.decode_header(raw_subj)))
    except Exception:
        subject = raw_subj
    from_email = _extract_addr(msg.get("From", ""))
    in_reply_to = _extract_msgid(msg.get("In-Reply-To", ""))
    references = msg.get("References", "") or ""
    message_id = _extract_msgid(msg.get("Message-ID", ""))
    received_at = _parse_date_header(msg.get("Date", "")) or dt.datetime.now().isoformat(
        timespec="seconds",
    )
    headers = {k: v for k, v in msg.items()}

    # Extract the readable text out of the MIME body. Gmail/Outlook often
    # wrap the real text in multipart/alternative with boundaries —
    # naively decoding gives useless "--boundary..." gunk. We feed HEADER
    # + TEXT back into email.message_from_bytes so the library walks the
    # parts and gives us the text/plain content.
    plain_text = ""
    try:
        full = email.message_from_bytes(header_raw + b"\r\n" + body_raw)
        for part in full.walk():
            ctype = (part.get_content_type() or "").lower()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                plain_text = payload.decode(charset, errors="replace").strip()
                if plain_text:
                    break
        # Fallback: HTML-only messages — strip tags crudely.
        if not plain_text:
            for part in full.walk():
                if (part.get_content_type() or "").lower() == "text/html":
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    plain_text = re.sub(r"<[^>]+>", "", html).strip()
                    break
    except Exception:
        pass
    if not plain_text:
        try:
            plain_text = (body_raw or b"").decode("utf-8", errors="replace").strip()
        except Exception:
            plain_text = ""
    # Strip common reply-quoting markers ("On ..., X wrote:" and below).
    cut = re.search(r"^On\s.+wrote:$", plain_text, re.MULTILINE)
    if cut:
        plain_text = plain_text[: cut.start()].rstrip()
    snippet = plain_text[:500]
    body = plain_text[:10_000]

    kind = _classify(from_email, subject, headers)
    return InboxMsg(
        uid=int(uid.decode() if isinstance(uid, bytes) else uid),
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        from_email=from_email,
        subject=subject,
        snippet=snippet,
        body=body,
        received_at=received_at,
        kind=kind,
    )


def _parse_date_header(raw: str) -> Optional[str]:
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        return parsed.isoformat(timespec="seconds")
    except Exception:
        return None


def poll_recent(account_id: int, since_uid: int = 0,
                max_fetch: int = 100) -> tuple[list[InboxMsg], int]:
    """Fetch messages from the given account's INBOX with UID > since_uid.
    Returns (messages, new_uid_seen). Caller persists new_uid_seen back on
    the account row."""
    creds = get_account_creds(account_id)
    if not creds:
        raise RuntimeError(f"Gmail account {account_id} not connected")
    email_addr, app_password = creds

    msgs: list[InboxMsg] = []
    new_high = since_uid

    m = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
    try:
        m.login(email_addr, app_password)
        m.select("INBOX", readonly=True)
        typ, data = m.uid("search", None, f"UID {since_uid + 1}:*")
        if typ != "OK":
            return [], since_uid
        uids = (data[0] or b"").split()
        uids = uids[-max_fetch:]
        for uid in uids:
            try:
                uid_int = int(uid.decode())
            except Exception:
                continue
            if uid_int <= since_uid:
                continue
            msg = _fetch_message(m, uid)
            if msg is None:
                continue
            msgs.append(msg)
            if msg.uid > new_high:
                new_high = msg.uid
    finally:
        try:
            m.logout()
        except Exception:
            pass

    return msgs, new_high
