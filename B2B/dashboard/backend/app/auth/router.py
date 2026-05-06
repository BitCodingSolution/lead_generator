"""Auth endpoints — login / me / logout, plus user management CRUD.

Every authenticated user can manage other users (no role tier yet — this
matches the existing single-tier auth model). Two safety rails on
delete:
  - You can't delete your own account (would lock you out mid-request).
  - You can't delete the last remaining user (would brick the system).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import users as user_store
from app.auth.jwt import issue_token
from app.auth.users import User, authenticate
from app.deps import require_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Login / me / logout
# ---------------------------------------------------------------------------


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
    return _serialize(user)


@router.post("/logout")
def logout() -> dict:
    # Stateless JWT — server has nothing to revoke. The frontend drops the
    # token from storage. Endpoint exists for symmetry / future blacklist.
    return {"ok": True}


# ---------------------------------------------------------------------------
# Users CRUD
# ---------------------------------------------------------------------------


class CreateUserIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)


class UpdateUserIn(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=80)
    password: str | None = Field(default=None, min_length=8, max_length=200)


def _serialize(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "created_at": u.created_at,
        "last_login_at": u.last_login_at,
    }


@router.get("/users")
def list_users(_: User = Depends(require_user)) -> dict:
    return {"users": [_serialize(u) for u in user_store.list_users()]}


@router.post("/users", status_code=201)
def create_user(body: CreateUserIn, _: User = Depends(require_user)) -> dict:
    try:
        u = user_store.create_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _serialize(u)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUserIn,
    _: User = Depends(require_user),
) -> dict:
    if body.username is None and body.password is None:
        raise HTTPException(400, "Provide username and/or password to update.")
    try:
        u = user_store.update_user(
            user_id, username=body.username, password=body.password,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if u is None:
        raise HTTPException(404, f"User {user_id} not found")
    return _serialize(u)


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current: User = Depends(require_user),
) -> dict:
    if user_id == current.id:
        raise HTTPException(400, "You cannot delete your own account.")
    if user_store.count_users() <= 1:
        raise HTTPException(400, "Cannot delete the last remaining user.")
    if not user_store.delete_user_by_id(user_id):
        raise HTTPException(404, f"User {user_id} not found")
    return {"ok": True, "deleted": user_id}
