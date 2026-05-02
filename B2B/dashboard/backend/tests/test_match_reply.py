"""
Tests for `_match_reply_to_lead` — the function that decides which lead
an inbound IMAP message belongs to. Misclassification here means a real
reply gets dropped on the floor, so we lock in every branch.
"""
from __future__ import annotations

import datetime as dt

import pytest

import linkedin_api as api


_NOW = "2026-04-30T19:50:00"


def _seed_lead(con, **overrides) -> int:
    """Insert one Sent lead with sensible defaults; returns its id."""
    defaults = {
        "post_url": f"https://x.test/{dt.datetime.now().timestamp()}",
        "posted_by": "Ayushi Sharma",
        "company": "Etoile Lune",
        "role": "Python developer",
        "email": "ayushi@etoilelune.com",
        "gen_subject": "Python + LangChain developer for your AI role",
        "gen_body": "Hi Ayushi, ...",
        "sent_message_id": "outbound.123@bitcoding.local",
        "sent_at": _NOW,
        "status": "Sent",
        # Schema-required NOT NULL columns. Same value for both — these
        # tests don't exercise the timeline so a fixed timestamp is fine.
        "first_seen_at": _NOW,
        "last_seen_at": _NOW,
    }
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    cur = con.execute(
        f"INSERT INTO leads ({cols}) VALUES ({placeholders})",
        list(defaults.values()),
    )
    con.commit()
    return cur.lastrowid


# ---- Tier 1: header-based matching ----------------------------------------


def test_match_by_in_reply_to_exact(db):
    lead_id = _seed_lead(db)
    got = api._match_reply_to_lead(
        db,
        in_reply_to="outbound.123@bitcoding.local",
        references="",
    )
    assert got == lead_id


def test_match_by_in_reply_to_with_brackets(db):
    lead_id = _seed_lead(db)
    got = api._match_reply_to_lead(
        db,
        in_reply_to="<outbound.123@bitcoding.local>",
        references="",
    )
    assert got == lead_id


def test_match_via_references_chain(db):
    """Some clients drop In-Reply-To but include the full References chain.
    The matcher must scan References too, not just In-Reply-To."""
    lead_id = _seed_lead(db)
    got = api._match_reply_to_lead(
        db,
        in_reply_to="",
        references="<other-msg@x.com> <outbound.123@bitcoding.local>",
    )
    assert got == lead_id


def test_match_unknown_message_id_returns_none(db):
    _seed_lead(db)
    got = api._match_reply_to_lead(
        db, in_reply_to="never-sent-this@x.com", references="",
        from_email="", subject="",  # no fallback inputs
    )
    assert got is None


# ---- Tier 2: subject + sender fallback -------------------------------------


def test_match_fallback_by_email_and_subject(db):
    """Gmail often rewrites our outbound Message-ID. The matcher falls
    back to (sender email + Re: original subject)."""
    lead_id = _seed_lead(db)
    got = api._match_reply_to_lead(
        db,
        in_reply_to="",
        references="",
        from_email="ayushi@etoilelune.com",
        subject="Re: Python + LangChain developer for your AI role",
    )
    assert got == lead_id


def test_match_fallback_handles_re_re_prefix(db):
    """A second-bounce reply might come back as 'Re: Re: ...'. The
    cleanup regex only strips a single 're:' — but the lead match also
    accepts startswith() on either side, so the prefix-doubled subject
    still resolves."""
    lead_id = _seed_lead(db)
    got = api._match_reply_to_lead(
        db,
        in_reply_to="",
        references="",
        from_email="ayushi@etoilelune.com",
        subject="Re: Re: Python + LangChain developer for your AI role",
    )
    assert got == lead_id


def test_match_fallback_case_insensitive_email(db):
    lead_id = _seed_lead(db)
    got = api._match_reply_to_lead(
        db,
        in_reply_to="",
        references="",
        from_email="Ayushi@EtoileLune.com",
        subject="Re: Python + LangChain developer for your AI role",
    )
    assert got == lead_id


def test_match_fallback_subject_whitespace_tolerant(db):
    lead_id = _seed_lead(db)
    got = api._match_reply_to_lead(
        db,
        in_reply_to="",
        references="",
        from_email="ayushi@etoilelune.com",
        subject="Re:    Python + LangChain  developer   for your AI role",
    )
    assert got == lead_id


def test_match_fallback_returns_none_when_no_lead_for_sender(db):
    """If the inbound is from someone we never wrote to, no match."""
    _seed_lead(db)
    got = api._match_reply_to_lead(
        db,
        in_reply_to="",
        references="",
        from_email="random@stranger.com",
        subject="Re: nothing",
    )
    assert got is None


def test_match_last_resort_single_sender(db):
    """If sender matches exactly one Sent lead and nothing else lines up,
    we still attribute. Documents the 'last resort' branch in the code."""
    lead_id = _seed_lead(db)
    got = api._match_reply_to_lead(
        db,
        in_reply_to="",
        references="",
        from_email="ayushi@etoilelune.com",
        subject="totally unrelated subject",
    )
    assert got == lead_id


def test_match_skips_archived_or_draft_leads(db):
    """Only Sent / Replied leads are considered for fallback matching —
    a Drafted lead with the same email shouldn't be picked."""
    _seed_lead(db, status="Drafted", email="drafted@etoilelune.com",
                gen_subject="Different subject")
    sent_id = _seed_lead(
        db,
        post_url="https://x.test/sent",
        email="ayushi@etoilelune.com",
        sent_message_id="other.id@bitcoding.local",
    )
    got = api._match_reply_to_lead(
        db,
        in_reply_to="",
        references="",
        from_email="ayushi@etoilelune.com",
        subject="Re: Python + LangChain developer for your AI role",
    )
    assert got == sent_id


# ---- _first_name_from_posted_by --------------------------------------------


def test_first_name_basic():
    assert api._first_name_from_posted_by("Ayushi Sharma") == "Ayushi"


def test_first_name_lowercase_capitalised():
    assert api._first_name_from_posted_by("ayushi sharma") == "Ayushi"


def test_first_name_empty():
    assert api._first_name_from_posted_by("") == ""
    assert api._first_name_from_posted_by("   ") == ""
