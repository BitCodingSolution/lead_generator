"""Bridge from `grab_leads/` scripts into the backend's SQLAlchemy stack.

`grab_leads/` is a sibling of `dashboard/backend/`. Scrapers and enrichers
need access to the backend's ORM models (`app.yc.models`, etc.) and its
shared engine, so this module:

  - puts `dashboard/backend/` on `sys.path` so `app.*` imports resolve
  - reads `DATABASE_URL` out of the backend's `.env` if it isn't already
    in the environment, before any engine is built
  - exposes `session_scope()` — a transactional context manager that
    commits on clean exit and rolls back on exception

Callers do:

    from common.db import session_scope
    from app.yc.models import YcLead

    with session_scope() as session:
        ...
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_BACKEND = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load_db_url_from_env_file() -> None:
    if os.environ.get("DATABASE_URL", "").strip():
        return
    env_path = _BACKEND / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                os.environ["DATABASE_URL"] = (
                    line.split("=", 1)[1].strip().strip('"').strip("'")
                )
                return
    raise RuntimeError(
        "DATABASE_URL not set. Add it to dashboard/backend/.env or export "
        "it in the environment before running grab_leads scripts."
    )


_load_db_url_from_env_file()

# Imported AFTER the env var is in place so the engine factory picks it up.
from app.linkedin.db import SessionLocal, get_engine  # noqa: E402


def _ensure_session_bound() -> None:
    eng = get_engine()
    if SessionLocal.kw.get("bind") is None:
        SessionLocal.configure(bind=eng)


@contextmanager
def session_scope() -> Iterator:
    """Scoped SQLAlchemy session: commits on clean exit, rolls back on exc."""
    _ensure_session_bound()
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
