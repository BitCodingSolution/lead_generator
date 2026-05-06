"""add yc_lead_sequence table

Revision ID: 6baf4601ee5d
Revises: 8da93b8b81bb
Create Date: 2026-05-06 19:58:56.955295

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6baf4601ee5d'
down_revision: Union[str, Sequence[str], None] = '8da93b8b81bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'yc_lead_sequence',
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('yc_lead_sequence')
