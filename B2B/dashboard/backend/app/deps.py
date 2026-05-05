"""Shared FastAPI dependencies.

Single auth scheme: a Bearer JWT issued by `/api/auth/login`.
"""
from __future__ import annotations

import jwt as pyjwt
from fastapi import Header, HTTPException, status

from app.auth.jwt import decode_token
from app.auth.users import User, get_user


def _parse_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def authenticate_request(authorization: str | None) -> User | None:
    """Validate the Bearer token, return the User or None.

    Used both by the security middleware (to gate every request) and by
    the `require_user` dependency (to surface the user to handlers).
    """
    token = _parse_bearer(authorization)
    if not token:
        return None
    try:
        claims = decode_token(token)
    except pyjwt.PyJWTError:
        return None
    try:
        user_id = int(claims.get("sub", "0"))
    except (TypeError, ValueError):
        return None
    return get_user(user_id)


def require_user(authorization: str | None = Header(default=None)) -> User:
    user = authenticate_request(authorization)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


__all__ = ["User", "require_user", "authenticate_request"]
