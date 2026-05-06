"""SQLAlchemy ORM models for the Y Combinator grab source.

Originally lived in a per-source SQLite file
(`grab_leads/sources/ycombinator/data.db`) with three tables:
`leads`, `founders`, `exported_leads`. They now live in Postgres
under the `yc_*` prefix, sharing the same engine / Alembic history
as the linkedin and marcel modules.

Class names use a `Yc` prefix to avoid SQLAlchemy registry collisions
with `Lead` (linkedin), `MarcelLead` (marcel), etc.

Type strategy mirrors the rest of the app: `Text` everywhere (including
ISO-string timestamps), `Integer` for booleans-as-int (`email_verified`,
`needs_attention`). Server-side defaults preserved where SQLite had them
so future direct INSERTs that omit a column still get the legacy value.
"""
from __future__ import annotations

from sqlalchemy import (
    Column, ForeignKey, Index, Integer, Text, UniqueConstraint,
)

from app.linkedin.db import Base


class YcLead(Base):
    """One row per scraped company. Source/source_url is the natural key."""

    __tablename__ = "yc_leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(Text, nullable=False)
    source_url = Column(Text, nullable=False)
    company_name = Column(Text, nullable=False)
    company_domain = Column(Text)
    company_size = Column(Text)
    location = Column(Text)
    signal_type = Column(Text, nullable=False)
    signal_detail = Column(Text)
    signal_date = Column(Text)
    person_name = Column(Text)
    person_title = Column(Text)
    person_linkedin = Column(Text)
    person_email = Column(Text)
    email_verified = Column(Integer, server_default="0")
    extra_data = Column(Text)  # JSON blob
    scraped_at = Column(Text)
    first_seen_at = Column(Text)
    last_seen_at = Column(Text)
    needs_attention = Column(Integer, server_default="0")
    # User-toggled "starred" flag, surfaced via the /star endpoint and the
    # `starred_only` filter on the multi-source leads listing. Backed by a
    # btree index so the filter stays cheap as the table grows.
    is_high_value = Column(Integer, server_default="0")

    __table_args__ = (
        UniqueConstraint("source", "source_url", name="uq_yc_leads_source_url"),
        Index("idx_yc_leads_signal", "signal_type"),
        Index("idx_yc_leads_domain", "company_domain"),
        Index("idx_yc_leads_attention", "needs_attention"),
        Index("idx_yc_leads_high_value", "is_high_value"),
    )


class YcFounder(Base):
    """Per-company founders / decision-makers — populated by enrich.py.
    Holds email-verification state so the picker knows who's mailable."""

    __tablename__ = "yc_founders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("yc_leads.id"), nullable=False)
    full_name = Column(Text)
    first_name = Column(Text)
    last_name = Column(Text)
    title = Column(Text)
    linkedin_url = Column(Text)
    twitter_url = Column(Text)
    bio = Column(Text)
    email = Column(Text)
    email_status = Column(Text)
    email_mx = Column(Text)
    candidates_tried = Column(Text)  # JSON
    extra_data = Column(Text)        # JSON
    enriched_at = Column(Text)

    __table_args__ = (
        UniqueConstraint("lead_id", "full_name", name="uq_yc_founders_lead_name"),
        Index("idx_yc_founders_lead", "lead_id"),
        Index("idx_yc_founders_email_status", "email_status"),
    )


class YcExportedLead(Base):
    """Tracks which (lead, founder) pairs have already been exported to a
    daily-batch XLSX. Prevents the export step from re-emitting the same
    rows on a subsequent run."""

    __tablename__ = "yc_exported_leads"

    # Composite PK. SQLite allowed NULL `founder_id` because of its loose
    # PK semantics; Postgres requires NOT NULL on PK columns, so callers
    # must always pass a real founder id (the export pipeline does).
    lead_id = Column(Integer, primary_key=True)
    founder_id = Column(Integer, primary_key=True)
    batch_file = Column(Text)
    exported_at = Column(Text)


class YcLeadSequence(Base):
    """Mirror of the legacy SQLite `sqlite_sequence` table.

    SQLite kept the next autoincrement id per table here. We carried the
    snapshot over so historical max-id values are still inspectable; the
    live next-id for `yc_leads` / `yc_founders` is now driven by the
    Postgres SERIAL sequences (`yc_leads_id_seq`, `yc_founders_id_seq`).
    """

    __tablename__ = "yc_lead_sequence"

    name = Column(Text, primary_key=True)
    seq = Column(Integer, nullable=False)
