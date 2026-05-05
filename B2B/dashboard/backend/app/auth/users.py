"""User store: Postgres (via SQLAlchemy + Alembic) + bcrypt hashing.

Uses the shared `linkedin_db.Base` so the auth table is part of the same
Alembic migration history as everything else, and rides the existing
engine/session created in `linkedin_db.init()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import Column, Index, Integer, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from linkedin_db import Base, SessionLocal, get_engine


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------

class UserRow(Base):
    """Internal ORM row. Public callers receive the `User` dataclass below."""

    __tablename__ = "dashboard_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    last_login_at = Column(Text)

    __table_args__ = (
        Index("idx_dashboard_users_username", "username"),
    )


# ---------------------------------------------------------------------------
# Public projection (what the rest of the app consumes)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class User:
    id: int
    username: str
    created_at: str
    last_login_at: str | None


def _project(row: UserRow) -> User:
    return User(
        id=row.id,
        username=row.username,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session() -> Session:
    # `linkedin_db.init()` configures SessionLocal's bind on app boot.
    # When this module is used outside that boot path (e.g. CLI scripts,
    # one-off migrations), bind it ourselves on first use.
    if SessionLocal.kw.get("bind") is None:
        SessionLocal.configure(bind=get_engine())
    return SessionLocal()


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# CRUD + authentication
# ---------------------------------------------------------------------------

def create_user(username: str, password: str) -> User:
    username = username.strip()
    if not username:
        raise ValueError("username is required")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    row = UserRow(
        username=username,
        password_hash=hash_password(password),
        created_at=_now(),
    )
    with _session() as s:
        s.add(row)
        try:
            s.commit()
        except IntegrityError as e:
            s.rollback()
            raise ValueError(f"username '{username}' already exists") from e
        s.refresh(row)
        return _project(row)


def set_password(username: str, password: str) -> bool:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    with _session() as s:
        row = (
            s.query(UserRow)
            .filter(UserRow.username.ilike(username.strip()))
            .one_or_none()
        )
        if row is None:
            return False
        row.password_hash = hash_password(password)
        s.commit()
        return True


def delete_user(username: str) -> bool:
    with _session() as s:
        row = (
            s.query(UserRow)
            .filter(UserRow.username.ilike(username.strip()))
            .one_or_none()
        )
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def list_users() -> list[User]:
    with _session() as s:
        rows = s.query(UserRow).order_by(UserRow.id).all()
        return [_project(r) for r in rows]


def authenticate(username: str, password: str) -> User | None:
    with _session() as s:
        row = (
            s.query(UserRow)
            .filter(UserRow.username.ilike(username.strip()))
            .one_or_none()
        )
        if row is None or not verify_password(password, row.password_hash):
            return None
        row.last_login_at = _now()
        s.commit()
        s.refresh(row)
        return _project(row)


def get_user(user_id: int) -> User | None:
    with _session() as s:
        row = s.get(UserRow, user_id)
        return _project(row) if row else None
