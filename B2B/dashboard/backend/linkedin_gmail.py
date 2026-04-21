"""
Gmail SMTP + IMAP helper for the LinkedIn module.

No Google Cloud / OAuth. The user generates an App Password at
https://myaccount.google.com/apppasswords and pastes it into
/linkedin/settings. We encrypt it at rest with Fernet and decrypt in-memory
only when sending / polling.

Public surface:
    get_credentials()  -> (email, app_password) | None
    save_credentials(email, app_password)
    clear_credentials()
    verify_credentials(email, app_password) -> dict  (runs live SMTP + IMAP login)
    send_email(to, subject, body, reply_to=..., thread_headers=...) -> dict
    poll_recent(since_uid) -> (list[InboxMsg], new_uid)   # Phase 5
"""
from __future__ import annotations

import datetime as dt
import email
import email.header
import email.utils
import imaplib
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

from linkedin_db import DB_PATH, connect

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


# --- credential store ------------------------------------------------------


def get_credentials() -> Optional[tuple[str, str]]:
    with connect() as con:
        r = con.execute(
            "SELECT email, app_password_enc FROM gmail_auth WHERE id = 1"
        ).fetchone()
    if not r or not r["email"] or not r["app_password_enc"]:
        return None
    return r["email"], _decrypt(r["app_password_enc"])


def save_credentials(email_addr: str, app_password: str) -> None:
    now = dt.datetime.now().isoformat(timespec="seconds")
    enc = _encrypt(app_password)
    with connect() as con:
        existing = con.execute(
            "SELECT 1 FROM gmail_auth WHERE id = 1"
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE gmail_auth SET email = ?, app_password_enc = ?, "
                "connected_at = COALESCE(connected_at, ?), last_verified_at = ? "
                "WHERE id = 1",
                (email_addr, enc, now, now),
            )
        else:
            con.execute(
                "INSERT INTO gmail_auth (id, email, app_password_enc, "
                "connected_at, last_verified_at) VALUES (1, ?, ?, ?, ?)",
                (email_addr, enc, now, now),
            )
        con.commit()


def clear_credentials() -> None:
    with connect() as con:
        con.execute("DELETE FROM gmail_auth WHERE id = 1")
        con.commit()


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


@dataclass
class InboxMsg:
    uid: int
    message_id: str
    in_reply_to: str       # upstream Message-ID this is replying to
    references: str        # full References header (space-separated IDs)
    from_email: str
    subject: str
    snippet: str
    received_at: str
    kind: str              # reply | bounce | auto_reply | unknown


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    reply_to: Optional[str] = None,
    attachment: Optional[tuple[Path, str]] = None,
) -> SendResult:
    """Send a plain-text email. If `attachment` is a (path, filename) tuple,
    the file is attached as PDF (only PDF attachments supported — matches the
    CV-picker design)."""
    creds = get_credentials()
    if not creds:
        raise RuntimeError("Gmail not connected")
    email_addr, app_password = creds

    # `mixed` when attaching, `alternative` otherwise — RFC 5322 layout.
    msg = MIMEMultipart("mixed" if attachment else "alternative")
    msg["From"] = email_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    bracketed = email.utils.make_msgid(domain="bitcoding.local")
    msg["Message-ID"] = bracketed
    msg_id = bracketed.strip("<>").strip()
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.attach(MIMEText(body, "plain", "utf-8"))

    if attachment:
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

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as s:
        s.login(email_addr, app_password)
        s.sendmail(email_addr, [to], msg.as_string())

    return SendResult(
        message_id=msg_id,
        sent_at=dt.datetime.now().isoformat(timespec="seconds"),
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
    if "auto-submitted" in {k.lower() for k in headers}:
        auto = str(headers.get("Auto-Submitted", "")).lower()
        if "auto-replied" in auto or "auto-generated" in auto:
            return "auto_reply"
    if _BOUNCE_SENDERS_RE.search(from_email or ""):
        return "bounce"
    if "X-Failed-Recipients" in headers or "X-Autoreply" in headers:
        return (
            "bounce"
            if "X-Failed-Recipients" in headers
            else "auto_reply"
        )
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
    # data[0] is (header_blob), data[1] is (text_blob); pyimap returns nested
    # tuples — flatten bytes.
    parts = [p for p in data if isinstance(p, tuple)]
    if not parts:
        return None
    header_raw = parts[0][1] if len(parts[0]) > 1 else b""
    body_raw = parts[1][1] if len(parts) > 1 and len(parts[1]) > 1 else b""
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

    snippet = ""
    try:
        snippet = (body_raw or b"").decode("utf-8", errors="replace")[:500].strip()
    except Exception:
        snippet = ""

    kind = _classify(from_email, subject, headers)
    return InboxMsg(
        uid=int(uid.decode() if isinstance(uid, bytes) else uid),
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        from_email=from_email,
        subject=subject,
        snippet=snippet,
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


def poll_recent(since_uid: int = 0, max_fetch: int = 100) -> tuple[list[InboxMsg], int]:
    """Fetch messages from INBOX with UID > since_uid. Returns (messages,
    new_uid_seen). Caller persists new_uid_seen back into gmail_auth."""
    creds = get_credentials()
    if not creds:
        raise RuntimeError("Gmail not connected")
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
