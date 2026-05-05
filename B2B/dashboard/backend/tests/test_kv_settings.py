"""Tests for the kv_settings runtime-config helpers."""
from __future__ import annotations

from app.linkedin import db as linkedin_db


def test_setting_returns_default_when_unset(db, monkeypatch):
    monkeypatch.delenv("DUMMY_X", raising=False)
    assert linkedin_db.get_setting_bool("dummy.x", env_key="DUMMY_X", default=False) is False
    assert linkedin_db.get_setting_bool("dummy.x", env_key="DUMMY_X", default=True) is True
    assert linkedin_db.get_setting_int("dummy.n", env_key="DUMMY_N", default=42) == 42


def test_setting_falls_back_to_env_when_db_unset(db, monkeypatch):
    monkeypatch.setenv("DUMMY_X", "1")
    assert linkedin_db.get_setting_bool("dummy.x", env_key="DUMMY_X", default=False) is True
    monkeypatch.setenv("DUMMY_X", "0")
    assert linkedin_db.get_setting_bool("dummy.x", env_key="DUMMY_X", default=True) is False
    monkeypatch.setenv("DUMMY_N", "7")
    assert linkedin_db.get_setting_int("dummy.n", env_key="DUMMY_N", default=42) == 7


def test_db_value_overrides_env(db, monkeypatch):
    """The whole point of the table: a UI-set value beats an env override
    so the user can change behaviour without touching config files."""
    monkeypatch.setenv("DUMMY_X", "0")
    linkedin_db.set_setting_raw("dummy.x", "true")
    assert linkedin_db.get_setting_bool("dummy.x", env_key="DUMMY_X", default=False) is True


def test_set_setting_upserts(db):
    linkedin_db.set_setting_raw("dummy.x", "true")
    linkedin_db.set_setting_raw("dummy.x", "false")
    assert linkedin_db.get_setting_raw("dummy.x") == "false"


def test_bool_recognises_truthy_strings(db):
    for v in ("1", "true", "TRUE", "yes", "on", " On "):
        linkedin_db.set_setting_raw("dummy.x", v)
        assert linkedin_db.get_setting_bool("dummy.x") is True, f"failed for {v!r}"


def test_bool_recognises_falsy_strings(db):
    for v in ("0", "false", "no", "off", "", "garbage"):
        linkedin_db.set_setting_raw("dummy.x", v)
        assert linkedin_db.get_setting_bool("dummy.x") is False, f"failed for {v!r}"


def test_int_handles_garbage_with_default(db):
    linkedin_db.set_setting_raw("dummy.n", "not-a-number")
    assert linkedin_db.get_setting_int("dummy.n", default=99) == 99
