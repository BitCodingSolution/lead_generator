"""Auth endpoints — login / me / logout."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.jwt import issue_token
from app.auth.users import User, authenticate
from app.deps import require_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginOut(BaseModel):
    access_token: str
    expires_at: str
    user: dict


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn) -> LoginOut:
    user = authenticate(body.username, body.password)
    if user is None:
        # Same response for unknown user vs. wrong password — don't leak which.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")
    token, exp = issue_token(user_id=user.id, username=user.username)
    return LoginOut(
        access_token=token,
        expires_at=exp.isoformat(),
        user={"id": user.id, "username": user.username},
    )


@router.get("/me")
def me(user: User = Depends(require_user)) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


@router.post("/logout")
def logout() -> dict:
    # Stateless JWT — server has nothing to revoke. The frontend drops the
    # token from storage. Endpoint exists for symmetry / future blacklist.
    return {"ok": True}
