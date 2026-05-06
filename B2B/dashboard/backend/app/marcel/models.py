"""SQLAlchemy ORM models for the Marcel outreach DB.

The data lives in Postgres as `mrc_*` tables; these models are the
single source of truth for the schema. They were ported from the
original SQLite source on disk during the cutover and now back the
runtime via `app.marcel.db.conn()`.

Built on the shared `Base` from `app.linkedin.db` so every domain
(linkedin, auth, marcel) lives in one Postgres database under one
Alembic history.

Naming
------
Table names carry an `mrc_` prefix so the marcel namespace is distinct
from `ln_` (linkedin) and `dashboard_users` (auth) when all three live
side by side in the same Postgres database.

Type choices
------------
- `Text` everywhere a string is stored — same convention the linkedin
  models use, including for ISO-8601 timestamp / date columns. Keeping
  storage as text means a future runtime cutover from sqlite3 to
  SQLAlchemy + Postgres won't require touching app logic that already
  passes ISO strings.
- `Integer` for booleans-as-int (`is_owner`, `opened`, `bounced`,
  `handled`, `email_valid`) — matches the existing SQLite schema and
  every call site's 0/1 handling.
- `Float` for `deals.value_eur` to match SQLite's REAL.

Defaults
--------
SQLite's `DEFAULT CURRENT_TIMESTAMP` columns map to nullable Text here.
Application code that previously relied on the SQL-level default will
need to start passing an explicit ISO timestamp on insert at the same
moment we cut over to SQLAlchemy. Until then, the existing sqlite3
runtime keeps using the on-disk database with its native defaults.
"""
from __future__ import annotations

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, Text

from app.linkedin.db import Base


class MarcelLead(Base):
    """Source-of-truth contact + company record. ~119k rows in production."""

    __tablename__ = "mrc_leads"

    lead_id = Column(Text, primary_key=True)
    name = Column(Text)
    salutation = Column(Text)
    title = Column(Text)
    company = Column(Text)
    email = Column(Text, nullable=False, unique=True)
    phone = Column(Text)
    xing = Column(Text)
    linkedin = Column(Text)
    industry = Column(Text)
    sub_industry = Column(Text)
    domain = Column(Text)
    website = Column(Text)
    city = Column(Text)
    dealfront_link = Column(Text)
    source_file = Column(Text)
    tier = Column(Integer)
    is_owner = Column(Integer)
    created_at = Column(Text)
    # Email validation columns added later via ALTER TABLE on SQLite —
    # all nullable since pre-validation rows had no value.
    email_valid = Column(Integer)
    email_invalid_reason = Column(Text)
    email_verified_at = Column(Text)

    __table_args__ = (
        Index("idx_mrc_leads_city", "city"),
        Index("idx_mrc_leads_industry", "industry"),
        Index("idx_mrc_leads_isowner", "is_owner"),
        Index("idx_mrc_leads_tier", "tier"),
    )


class MarcelLeadStatus(Base):
    """1:1 sidecar for `Lead`: pipeline state, scheduled action, owner.
    Split off so high-churn columns don't bloat the leads row."""

    __tablename__ = "mrc_lead_status"

    lead_id = Column(Text, ForeignKey("mrc_leads.lead_id"), primary_key=True)
    status = Column(Text, server_default="New")
    touch_count = Column(Integer, server_default="0")
    last_touch_date = Column(Text)
    next_action = Column(Text)
    next_action_date = Column(Text)
    first_sent_at = Column(Text)
    assigned_to = Column(Text, server_default="Pradip")
    tags = Column(Text)
    updated_at = Column(Text)

    __table_args__ = (
        Index("idx_mrc_status_current", "status"),
        Index("idx_mrc_status_next_date", "next_action_date"),
    )


class MarcelEmailSent(Base):
    """One row per outbound email. `touch_number` is 1, 2, … per lead."""

    __tablename__ = "mrc_emails_sent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Text, ForeignKey("mrc_leads.lead_id"), nullable=False)
    batch_date = Column(Text)
    touch_number = Column(Integer)
    subject = Column(Text)
    body = Column(Text)
    from_email = Column(Text)
    sent_at = Column(Text)
    outlook_entry_id = Column(Text)
    opened = Column(Integer, server_default="0")
    bounced = Column(Integer, server_default="0")
    bounce_reason = Column(Text)

    __table_args__ = (
        Index("idx_mrc_emails_lead", "lead_id"),
    )


class MarcelReply(Base):
    """Inbound replies — populated by the Outlook poller, triaged via
    `handled` / `my_response`."""

    __tablename__ = "mrc_replies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Text, ForeignKey("mrc_leads.lead_id"), nullable=False)
    reply_at = Column(Text)
    subject = Column(Text)
    body = Column(Text)
    sentiment = Column(Text)
    snippet = Column(Text)
    handled = Column(Integer, server_default="0")
    handled_at = Column(Text)
    my_response = Column(Text)

    __table_args__ = (
        Index("idx_mrc_replies_lead", "lead_id"),
        Index("idx_mrc_replies_pending", "handled"),
    )


class MarcelDailyBatch(Base):
    """One row per send day — drives the daily pipeline KPIs."""

    __tablename__ = "mrc_daily_batches"

    batch_date = Column(Text, primary_key=True)
    leads_picked = Column(Integer)
    drafts_generated = Column(Integer)
    sent_count = Column(Integer)
    replies_count = Column(Integer)
    notes = Column(Text)


class MarcelDeal(Base):
    """Closed-won / closed-lost outcomes. `value_eur` mirrors SQLite REAL."""

    __tablename__ = "mrc_deals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Text, ForeignKey("mrc_leads.lead_id"), nullable=False)
    stage = Column(Text)
    value_eur = Column(Float)
    signed_at = Column(Text)
    lost_reason = Column(Text)


class MarcelDoNotContact(Base):
    """Suppression list — emails that must never be contacted again."""

    __tablename__ = "mrc_do_not_contact"

    email = Column(Text, primary_key=True)
    reason = Column(Text)
    added_at = Column(Text)


class MarcelMeeting(Base):
    """Scheduled / past meetings booked off a lead conversation."""

    __tablename__ = "mrc_meetings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Text, ForeignKey("mrc_leads.lead_id"), nullable=False)
    scheduled_at = Column(Text)
    duration_min = Column(Integer)
    outcome = Column(Text)
    notes = Column(Text)


class MarcelNote(Base):
    """Free-form notes attached to a lead."""

    __tablename__ = "mrc_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Text, ForeignKey("mrc_leads.lead_id"), nullable=False)
    note = Column(Text)
    created_at = Column(Text)
    created_by = Column(Text)
