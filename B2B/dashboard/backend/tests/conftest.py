"""
Shared pytest fixtures for the LinkedIn dashboard backend.

For PostgreSQL: tests create/drop a `leads_test` database per session
and patch linkedin_db's engine to point there. The safety_state singleton
is seeded via linkedin_db.init().
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# Make `dashboard/backend/` importable regardless of where pytest is invoked.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Load .env so DATABASE_URL is available for test DB creation.
try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass


def _test_db_url() -> str:
    """Build a test database URL from the production one, swapping the
    db name for '<name>_test'."""
    base = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/linkedin_leads",
    )
    # Replace the last path segment (db name) with <name>_test
    parts = base.rsplit("/", 1)
    return parts[0] + "/" + parts[1] + "_test"


@pytest.fixture(scope="session", autouse=True)
def _create_test_db():
    """Create the test database once per session; drop it after."""
    from sqlalchemy import create_engine, text as sa_text

    test_url = _test_db_url()
    db_name = test_url.rsplit("/", 1)[1]
    admin_url = test_url.rsplit("/", 1)[0] + "/postgres"

    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        # Drop if leftover from a previous aborted run
        conn.execute(sa_text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    eng.dispose()

    yield

    # Dispose linkedin_db's cached engine so it releases all pool connections
    import linkedin_db
    if linkedin_db._engine is not None:
        linkedin_db._engine.dispose()
        linkedin_db._engine = None

    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        # Terminate any lingering connections (from TestClient, etc.)
        conn.execute(sa_text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
        ))
        conn.execute(sa_text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    eng.dispose()


@pytest.fixture
def db(monkeypatch):
    """Fresh schema in the test database; patches linkedin_db's engine.
    Yields a live PgConnection (callers can use it directly to seed rows;
    production code calls connect() again, which opens the same PG db)."""
    import linkedin_db

    test_url = _test_db_url()

    # Force a fresh engine pointing at the test DB
    from sqlalchemy import create_engine
    test_engine = create_engine(test_url, pool_pre_ping=True)

    # Reset the cached engine
    monkeypatch.setattr(linkedin_db, "_engine", test_engine)

    # Wipe and recreate all tables for a clean slate
    linkedin_db.Base.metadata.drop_all(test_engine)
    linkedin_db.Base.metadata.create_all(test_engine)

    # Seed the safety_state singleton
    linkedin_db.SessionLocal.configure(bind=test_engine)
    with linkedin_db.connect() as con:
        linkedin_db.ensure_safety_row(con)
        con.commit()

    with linkedin_db.connect() as con:
        yield con
