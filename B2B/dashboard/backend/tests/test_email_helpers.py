"""Pure-function tests for the IMAP polling helpers — no DB, no network."""
from __future__ import annotations

import linkedin_gmail as gmail


# ---- _extract_addr ---------------------------------------------------------


def test_extract_addr_strips_display_name():
    assert gmail._extract_addr("Ayushi Sharma <ayushi@etoilelune.com>") == "ayushi@etoilelune.com"


def test_extract_addr_handles_bare_email():
    assert gmail._extract_addr("ayushi@etoilelune.com") == "ayushi@etoilelune.com"


def test_extract_addr_strips_brackets_when_no_display_name():
    assert gmail._extract_addr("<ayushi@etoilelune.com>") == "ayushi@etoilelune.com"


def test_extract_addr_empty_returns_empty():
    assert gmail._extract_addr("") == ""
    assert gmail._extract_addr(None) == ""  # type: ignore[arg-type]


# ---- _extract_msgid --------------------------------------------------------


def test_extract_msgid_strips_brackets():
    assert gmail._extract_msgid("<19ddec7e7ec.429e32b6@etoilelune.co>") == "19ddec7e7ec.429e32b6@etoilelune.co"


def test_extract_msgid_handles_bare_id():
    assert gmail._extract_msgid("19ddec7e7ec@etoilelune.co") == "19ddec7e7ec@etoilelune.co"


def test_extract_msgid_strips_whitespace():
    assert gmail._extract_msgid("  <abc@x.com>  ") == "abc@x.com"


def test_extract_msgid_empty():
    assert gmail._extract_msgid("") == ""
    assert gmail._extract_msgid(None) == ""  # type: ignore[arg-type]


# ---- _classify -------------------------------------------------------------


def test_classify_mailer_daemon_is_bounce():
    assert gmail._classify("mailer-daemon@google.com", "Delivery failure", {}) == "bounce"


def test_classify_postmaster_is_bounce():
    assert gmail._classify("postmaster@example.com", "Mail delivery failure", {}) == "bounce"


def test_classify_mail_delivery_subdomain_is_bounce():
    assert gmail._classify("mail-delivery@bounce.example.com", "Undeliverable", {}) == "bounce"


def test_classify_xfailed_recipients_header_is_bounce():
    assert gmail._classify("anyone@example.com", "Re: hi", {"X-Failed-Recipients": "x@y.z"}) == "bounce"


def test_classify_bounce_wins_over_auto_replied_header():
    """Real-world NDRs often carry Auto-Submitted headers — bounce
    classification must take precedence so we count them as bounces, not
    OOO. Regression test for the comment in linkedin_gmail._classify."""
    assert gmail._classify(
        "mailer-daemon@google.com",
        "Delivery Status Notification",
        {"Auto-Submitted": "auto-replied"},
    ) == "bounce"


def test_classify_auto_submitted_header_is_auto_reply():
    assert gmail._classify(
        "alice@example.com", "Re: hi", {"Auto-Submitted": "auto-replied"}
    ) == "auto_reply"


def test_classify_xautoreply_header_is_auto_reply():
    assert gmail._classify("alice@example.com", "Re: hi", {"X-Autoreply": "yes"}) == "auto_reply"


def test_classify_out_of_office_subject_is_auto_reply():
    assert gmail._classify("alice@example.com", "Out of Office: Re: hi", {}) == "auto_reply"


def test_classify_automatic_reply_subject_is_auto_reply():
    assert gmail._classify("alice@example.com", "Automatic reply: vacation", {}) == "auto_reply"


def test_classify_normal_human_reply():
    assert gmail._classify("ayushi@etoilelune.com", "Re: Python role", {}) == "reply"


def test_classify_handles_empty_inputs():
    assert gmail._classify("", "", {}) == "reply"


# ---- effective_cap (warmup curve enforcement) ------------------------------


def test_effective_cap_disabled_returns_raw_cap():
    cap = gmail.effective_cap(50, warmup_enabled=False, warmup_start_date="2026-01-01")
    assert cap == 50


# Pass `curve=` explicitly in every test below so the assertions don't
# depend on whatever the production DB has in safety_state.warmup_curve_json.
_DEFAULT_CURVE = list(gmail.DEFAULT_WARMUP_CURVE)


def test_effective_cap_first_day_is_clamped():
    """Day 0 of a fresh account: cap should match the first curve stage,
    not the user's daily_cap."""
    today = gmail.dt.date.today().isoformat()
    cap = gmail.effective_cap(50, True, today, curve=_DEFAULT_CURVE)
    # Default curve: day 0 -> 5
    assert cap == 5


def test_effective_cap_fully_warm_returns_raw_cap():
    """Once past the longest stage, the user's daily_cap takes over."""
    long_ago = (gmail.dt.date.today() - gmail.dt.timedelta(days=30)).isoformat()
    cap = gmail.effective_cap(50, True, long_ago, curve=_DEFAULT_CURVE)
    assert cap == 50


def test_effective_cap_respects_user_cap_below_curve():
    """User-configured cap of 3 must not be exceeded by the warmup curve."""
    today = gmail.dt.date.today().isoformat()
    cap = gmail.effective_cap(3, True, today, curve=_DEFAULT_CURVE)
    assert cap == 3


def test_effective_cap_uses_explicit_curve():
    today = gmail.dt.date.today().isoformat()
    custom = [(1, 2), (5, 8)]
    assert gmail.effective_cap(100, True, today, curve=custom) == 2
    seven_days = (gmail.dt.date.today() - gmail.dt.timedelta(days=7)).isoformat()
    assert gmail.effective_cap(100, True, seven_days, curve=custom) == 100


# ---- _days_since -----------------------------------------------------------


def test_days_since_today_is_zero():
    today = gmail.dt.date.today().isoformat()
    assert gmail._days_since(today) == 0


def test_days_since_yesterday_is_one():
    y = (gmail.dt.date.today() - gmail.dt.timedelta(days=1)).isoformat()
    assert gmail._days_since(y) == 1


def test_days_since_none_treats_as_warm():
    """Caller convention: missing start date means 'fully warm' (returns
    14 so the warmup curve no-ops)."""
    assert gmail._days_since(None) == 14


def test_days_since_garbage_treats_as_warm():
    assert gmail._days_since("not-a-date") == 14
