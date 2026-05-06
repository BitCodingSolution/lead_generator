"""rename linkedin tables with ln_ prefix

Revision ID: dd92069c8aae
Revises: 61e6dc8d0394
Create Date: 2026-05-06 15:26:57.202011

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd92069c8aae'
down_revision: Union[str, Sequence[str], None] = '61e6dc8d0394'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LINKEDIN_TABLES = (
    "archived_urls",
    "autopilot_runs",
    "blocklist",
    "company_enrichment",
    "cvs",
    "email_opens",
    "events",
    "extension_keys",
    "followups",
    "gmail_accounts",
    "kv_settings",
    "leads",
    "recyclebin",
    "replies",
    "safety_state",
)


def upgrade() -> None:
    """Add `ln_` prefix to every linkedin-domain table."""
    for name in _LINKEDIN_TABLES:
        op.rename_table(name, f"ln_{name}")


def downgrade() -> None:
    """Strip the `ln_` prefix to restore the original names."""
    for name in _LINKEDIN_TABLES:
        op.rename_table(f"ln_{name}", name)
