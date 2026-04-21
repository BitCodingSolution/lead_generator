"""
Lightweight email verifier: syntax + MX + disposable + role check.

Intentionally NO SMTP handshake (too many providers block, false positives
from catch-alls, slow, risks our sender IP). MX-present + clean syntax +
non-role is the safe free signal.

Returns verdict dict:
  { 'email': ..., 'status': 'ok'|'invalid'|'role'|'disposable'|'no_mx',
    'reason': ..., 'mx_host': ... }
"""
from __future__ import annotations

import re
from functools import lru_cache

import dns.resolver


EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

DISPOSABLE = {
    "mailinator.com", "tempmail.com", "temp-mail.org", "10minutemail.com",
    "guerrillamail.com", "yopmail.com", "throwaway.email", "trashmail.com",
    "sharklasers.com", "maildrop.cc", "fakeinbox.com", "dispostable.com",
    "getairmail.com", "emailondeck.com", "spamgourmet.com", "mytrashmail.com",
}

ROLE_PREFIXES = {
    "info", "office", "admin", "support", "sales", "noreply", "no-reply",
    "hello", "service", "webmaster", "postmaster", "marketing", "hr",
    "jobs", "career", "mail", "newsletter", "press", "contact", "team",
}


@lru_cache(maxsize=2048)
def _mx_lookup(domain: str, timeout: float = 5.0) -> str | None:
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = timeout
        answers = resolver.resolve(domain, "MX")
        if answers:
            sorted_mx = sorted(answers, key=lambda r: r.preference)
            return str(sorted_mx[0].exchange).rstrip(".")
    except Exception:
        return None
    return None


def verify(email: str) -> dict:
    email = (email or "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        return {"email": email, "status": "invalid", "reason": "bad_syntax", "mx_host": None}
    local, _, domain = email.partition("@")
    if local in ROLE_PREFIXES:
        return {"email": email, "status": "role", "reason": f"role:{local}", "mx_host": None}
    if domain in DISPOSABLE:
        return {"email": email, "status": "disposable", "reason": "disposable_domain", "mx_host": None}
    mx = _mx_lookup(domain)
    if not mx:
        return {"email": email, "status": "no_mx", "reason": "no_mx_record", "mx_host": None}
    return {"email": email, "status": "ok", "reason": "", "mx_host": mx}
