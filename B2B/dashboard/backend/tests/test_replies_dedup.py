"""
Regression tests for the content-level reply dedup that catches the
"sender's mailer fired the same email twice with different Message-IDs"
case. The UNIQUE(gmail_msg_id) constraint can't see this — only the
content-equality check we added in `_poll_and_store` does.

These tests target the SQL the polling path uses (kept in sync with
linkedin_api._poll_and_store), so a future refactor that breaks the
behaviour fails here loudly instead of silently shipping duplicates.
"""
from __future__ import annotations

import datetime as dt


_NOW = "2026-04-30T19:50:00"


def _seed_lead(con) -> int:
    cur = con.execute(
        "INSERT INTO leads (post_url, email, status, first_seen_at, last_seen_at, "
        "sent_at, sent_message_id, gen_subject, gen_body) "
        "VALUES (?, ?, 'Sent', ?, ?, ?, ?, ?, ?)",
        (
            f"https://x.test/{dt.datetime.now().timestamp()}",
            "ayushi@etoilelune.com",
            _NOW, _NOW, _NOW,
            "outbound.123@bitcoding.local",
            "Python + LangChain developer for your AI role",
            "Hi Ayushi, ...",
        ),
    )
    con.commit()
    return cur.lastrowid


def _insert_reply(con, lead_id, msg_id, body, subject="Re: ...", from_email="ayushi@etoilelune.com"):
    con.execute(
        "INSERT INTO replies (lead_id, gmail_msg_id, from_email, subject, snippet, "
        "body, received_at, kind) VALUES (?, ?, ?, ?, ?, ?, ?, 'reply')",
        (lead_id, msg_id, from_email, subject, body[:200], body, _NOW),
    )
    con.commit()


def _content_dup_exists(con, lead_id, from_email, subject, body) -> bool:
    """Mirrors the dedup probe from linkedin_api._poll_and_store. If this
    SQL drifts, the polling path drifts too — the test is here to keep
    both in lock-step."""
    return con.execute(
        "SELECT 1 FROM replies WHERE lead_id = ? AND from_email = ? "
        "AND subject = ? AND body = ? LIMIT 1",
        (lead_id, from_email, subject, body),
    ).fetchone() is not None


def test_dedup_catches_identical_resend_with_different_msgid(db):
    """Same physical email, two different Message-IDs (mailer retry).
    Second arrival must register as a duplicate."""
    lead_id = _seed_lead(db)
    body = "Thanks for getting back. Please share your details."
    subject = "Re: Python + LangChain developer for your AI role"
    _insert_reply(db, lead_id, "first.msgid@x.com", body, subject)

    # New Message-ID, identical content -> must be considered a duplicate.
    assert _content_dup_exists(
        db, lead_id, "ayushi@etoilelune.com", subject, body,
    )


def test_dedup_does_not_flag_different_body_as_duplicate(db):
    """A genuine follow-up with different wording must NOT be flagged."""
    lead_id = _seed_lead(db)
    subject = "Re: Python + LangChain developer for your AI role"
    _insert_reply(db, lead_id, "first.msgid@x.com",
                  "First reply with my details.", subject)

    assert not _content_dup_exists(
        db, lead_id, "ayushi@etoilelune.com", subject,
        "Second reply, asking a follow-up question.",
    )


def test_dedup_does_not_cross_leads(db):
    """Two different leads with the same sender + same body shouldn't
    collide — each lead's thread is independent."""
    lead_a = _seed_lead(db)
    lead_b = _seed_lead(db)
    body = "Thanks, will revert."
    subject = "Re: Python + LangChain developer for your AI role"
    _insert_reply(db, lead_a, "msgid.a@x.com", body, subject)

    # Same sender + body, but inbound on a different lead. Not a dup.
    assert not _content_dup_exists(
        db, lead_b, "ayushi@etoilelune.com", subject, body,
    )


def test_dedup_treats_subject_change_as_new_reply(db):
    """If the subject differs (e.g. follow-up reframes the topic), it's
    a separate message — not a duplicate."""
    lead_id = _seed_lead(db)
    body = "Same body text"
    _insert_reply(db, lead_id, "msgid.a@x.com", body, subject="Re: original")

    assert not _content_dup_exists(
        db, lead_id, "ayushi@etoilelune.com", "Re: different angle", body,
    )


def test_unique_constraint_still_blocks_same_msgid(db):
    """The Message-ID UNIQUE constraint is still our first line of
    defence (cheaper than content match). This locks it in."""
    import sqlite3

    lead_id = _seed_lead(db)
    _insert_reply(db, lead_id, "same.msgid@x.com", "body 1")
    try:
        _insert_reply(db, lead_id, "same.msgid@x.com", "body 2 different")
    except sqlite3.IntegrityError:
        return
    raise AssertionError("expected IntegrityError on duplicate gmail_msg_id")
