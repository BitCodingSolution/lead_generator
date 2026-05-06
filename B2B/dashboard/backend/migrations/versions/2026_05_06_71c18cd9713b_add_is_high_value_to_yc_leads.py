"""add is_high_value to yc_leads

Revision ID: 71c18cd9713b
Revises: 6baf4601ee5d
Create Date: 2026-05-06 21:07:10.454986

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71c18cd9713b'
down_revision: Union[str, Sequence[str], None] = '6baf4601ee5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'yc_leads',
        sa.Column('is_high_value', sa.Integer(), server_default='0', nullable=True),
    )
    op.create_index(
        'idx_yc_leads_high_value', 'yc_leads', ['is_high_value'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_yc_leads_high_value', table_name='yc_leads')
    op.drop_column('yc_leads', 'is_high_value')
