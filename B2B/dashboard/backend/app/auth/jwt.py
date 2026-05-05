"""JWT issue/verify for session tokens.

HS256 signing with a secret read from `DASHBOARD_JWT_SECRET` env var, or
auto-generated and persisted to `<backend>/.jwt_secret` on first boot
(same pattern as the legacy `.api_key` file).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt

from app.config import settings


_SESSION_TTL = timedelta(hours=12)
_ALGO = "HS256"


def _secret_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".jwt_secret"


_cached_secret: str | None = None


def _secret() -> str:
    global _cached_secret
    if _cached_secret:
        return _cached_secret
    if settings.dashboard_jwt_secret.strip():
        _cached_secret = settings.dashboard_jwt_secret.strip()
        return _cached_secret
    p = _secret_path()
    if p.exists():
        s = p.read_text(encoding="utf-8").strip()
        if s:
            _cached_secret = s
            return s
    s = secrets.token_urlsafe(48)
    try:
        p.write_text(s, encoding="utf-8")
    except OSError:
        pass
    _cached_secret = s
    return s


def issue_token(*, user_id: int, username: str) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    exp = now + _SESSION_TTL
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = pyjwt.encode(payload, _secret(), algorithm=_ALGO)
    return token, exp


def decode_token(token: str) -> dict:
    """Returns the claims dict on success; raises pyjwt.PyJWTError on failure."""
    return pyjwt.decode(token, _secret(), algorithms=[_ALGO], options={"require": ["exp", "sub"]})
