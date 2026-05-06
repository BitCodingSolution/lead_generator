"""
Tests for the multi-account Gmail bookkeeping — daily counters,
auto-pause thresholds, cooldown logic, and warmup curve persistence.
These are the rails that prevent the system from blowing past safe send
volumes, so they need lock-in coverage.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.linkedin.services import gmail


# ---- _cooldown_remaining_s -------------------------------------------------


def test_cooldown_zero_when_no_last_sent():
    assert gmail._cooldown_remaining_s(None, dt.datetime.now()) == 0
    assert gmail._cooldown_remaining_s("", dt.datetime.now()) == 0


def test_cooldown_zero_when_long_past():
    long_ago = (dt.datetime.now() - dt.timedelta(hours=1)).isoformat()
    assert gmail._cooldown_remaining_s(long_ago, dt.datetime.now()) == 0


def test_cooldown_full_window_when_just_sent():
    """Just sent -> the full MIN_ACCOUNT_GAP_S window remains."""
    just_now = dt.datetime.now().isoformat()
    remaining = gmail._cooldown_remaining_s(just_now, dt.datetime.now())
    # The clock can tick between the two calls; allow 1s slop.
    assert remaining >= gmail.MIN_ACCOUNT_GAP_S - 1


def test_cooldown_partial_window():
    half_gap = gmail.MIN_ACCOUNT_GAP_S // 2
    earlier = (dt.datetime.now() - dt.timedelta(seconds=half_gap)).isoformat()
    remaining = gmail._cooldown_remaining_s(earlier, dt.datetime.now())
    # Should be ~half the gap, give or take a second.
    assert abs(remaining - half_gap) <= 2


def test_cooldown_garbage_timestamp_fails_open():
    """Bad ISO -> treated as 'no recent send' so the picker doesn't get
    permanently stuck on an account with corrupted state."""
    assert gmail._cooldown_remaining_s("not-an-iso", dt.datetime.now()) == 0


# ---- save_warmup_curve / get_warmup_curve ---------------------------------


def test_warmup_curve_roundtrip(db):
    custom = [(1, 3), (5, 8), (14, 25)]
    gmail.save_warmup_curve(custom)
    got = gmail.get_warmup_curve()
    assert got == custom


def test_warmup_curve_normalises_ordering(db):
    """Caller can pass curve out of order — storage always sorts by day."""
    gmail.save_warmup_curve([(14, 25), (1, 3), (5, 8)])
    got = gmail.get_warmup_curve()
    assert [d for d, _ in got] == [1, 5, 14]


def test_warmup_curve_rejects_non_positive(db):
    """0/negative entries get filtered out; if nothing's left we raise."""
    with pytest.raises(ValueError):
        gmail.save_warmup_curve([(0, 0), (-1, -1)])


def test_warmup_curve_falls_back_to_default_when_unset(db):
    """Fresh DB -> get_warmup_curve returns the module default."""
    got = gmail.get_warmup_curve()
    assert got == list(gmail.DEFAULT_WARMUP_CURVE)


# ---- record_send_failure (auto-pause rail) --------------------------------


def _seed_account(con, **overrides) -> int:
    """Insert one active gmail_accounts row; return id."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    defaults = {
        "email": f"test+{dt.datetime.now().timestamp()}@example.com",
        "app_password_enc": "fake-encrypted-blob",
        "display_name": "Test",
        "daily_cap": 50,
        "sent_today": 0,
        "sent_date": dt.date.today().isoformat(),
        "imap_uid_seen": 0,
        "status": "active",
        "warmup_enabled": 0,
        "connected_at": now,
        "last_verified_at": now,
        "consecutive_failures": 0,
        "bounce_count_today": 0,
    }
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    cur = con.execute(
        f"INSERT INTO ln_gmail_accounts ({cols}) VALUES ({placeholders})",
        list(defaults.values()),
    )
    con.commit()
    return cur.lastrowid


def test_record_send_failure_increments_counter(db):
    aid = _seed_account(db)
    paused = gmail.record_send_failure(aid, "smtp timeout")
    assert paused is False
    row = db.execute(
        "SELECT consecutive_failures, status FROM ln_gmail_accounts WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["consecutive_failures"] == 1
    assert row["status"] == "active"


def test_record_send_failure_auto_pauses_at_threshold(db):
    """Hit the threshold -> account flips to paused with a reason."""
    aid = _seed_account(db)
    for i in range(gmail.AUTO_PAUSE_FAILURE_THRESHOLD - 1):
        assert gmail.record_send_failure(aid, "smtp x") is False
    assert gmail.record_send_failure(aid, "final smtp x") is True
    row = db.execute(
        "SELECT status, paused_reason FROM ln_gmail_accounts WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["status"] == "paused"
    assert "SMTP failure" in row["paused_reason"]


def test_record_bounce_auto_pauses_at_threshold(db):
    aid = _seed_account(db)
    for i in range(gmail.AUTO_PAUSE_BOUNCE_THRESHOLD - 1):
        assert gmail.record_bounce(aid) is False
    assert gmail.record_bounce(aid) is True
    row = db.execute(
        "SELECT status, paused_reason, bounce_count_today FROM ln_gmail_accounts WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["status"] == "paused"
    assert row["bounce_count_today"] == gmail.AUTO_PAUSE_BOUNCE_THRESHOLD
    assert "bounces today" in row["paused_reason"]


def test_record_bounce_returns_false_for_unknown_account(db):
    assert gmail.record_bounce(99999) is False


def test_set_account_status_active_clears_failure_counters(db):
    """Manual resume should clear BOTH the consecutive-failure counter and
    the bounce-today counter — without that the account would re-pause on
    the next failure even after the user explicitly resumed."""
    aid = _seed_account(db, status="paused", consecutive_failures=5,
                         bounce_count_today=3, paused_reason="test")
    gmail.set_account_status(aid, "active")
    row = db.execute(
        "SELECT status, consecutive_failures, bounce_count_today, paused_reason "
        "FROM ln_gmail_accounts WHERE id = ?", (aid,),
    ).fetchone()
    assert row["status"] == "active"
    assert row["consecutive_failures"] == 0
    assert row["bounce_count_today"] == 0
    assert row["paused_reason"] is None


# ---- _roll_if_stale_day ----------------------------------------------------


def test_roll_if_stale_day_resets_counters(db):
    """When sent_date != today, both counters zero out in one pass."""
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    aid = _seed_account(db, sent_today=15, sent_date=yesterday,
                         bounce_count_today=2)
    gmail._roll_if_stale_day(db, dt.date.today().isoformat())
    db.commit()
    row = db.execute(
        "SELECT sent_today, bounce_count_today, sent_date "
        "FROM ln_gmail_accounts WHERE id = ?", (aid,),
    ).fetchone()
    assert row["sent_today"] == 0
    assert row["bounce_count_today"] == 0
    assert row["sent_date"] == dt.date.today().isoformat()


def test_roll_if_stale_day_leaves_today_alone(db):
    today = dt.date.today().isoformat()
    aid = _seed_account(db, sent_today=15, sent_date=today,
                         bounce_count_today=2)
    gmail._roll_if_stale_day(db, today)
    db.commit()
    row = db.execute(
        "SELECT sent_today, bounce_count_today FROM ln_gmail_accounts WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["sent_today"] == 15
    assert row["bounce_count_today"] == 2
