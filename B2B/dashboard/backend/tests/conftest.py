"""
Shared pytest fixtures for the LinkedIn dashboard backend.

The biggest hazard with these tests is accidentally pointing at the real
SQLite file at H:/Lead Generator/B2B/Database/LinkedIn Data/leads.db. We
guard against that by:
  1. Patching linkedin_db.DB_PATH to a tmp_path file BEFORE importing any
     module that reads it.
  2. Reloading linkedin_db after the patch so the new DB_PATH sticks.
  3. Calling linkedin_db.init() against the tmp path so we get the real
     schema (no drift between prod and tests).

Tests that just exercise pure helpers (regexes, parsers, math) don't need
the DB at all and skip the fixture.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Make `dashboard/backend/` importable regardless of where pytest is invoked.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh schema in a tmp file; patches linkedin_db.DB_PATH so any
    `with connect()` call routes here. Yields a live connection (callers
    can use it directly to seed rows; production code calls connect()
    again, which opens the same file)."""
    import linkedin_db  # noqa: WPS433 — late import after sys.path tweak
    test_db = tmp_path / "leads.db"
    monkeypatch.setattr(linkedin_db, "DB_PATH", test_db)
    importlib.reload(linkedin_db)  # ensure module-level helpers see the new path
    monkeypatch.setattr(linkedin_db, "DB_PATH", test_db)
    linkedin_db.init()
    with linkedin_db.connect() as con:
        yield con
