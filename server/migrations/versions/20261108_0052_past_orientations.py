"""Orientations a camera had before it was moved (SPEC §10.8): fireballs sent later are
checked against them too

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-08 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0052'
down_revision: str | None = '0051'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'past_orientation',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('device_id', sa.String(length=26), nullable=False),
        sa.Column('orientation', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('until', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['device.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_past_orientation_device_id', 'past_orientation', ['device_id'])


def downgrade() -> None:
    op.drop_index('ix_past_orientation_device_id', table_name='past_orientation')
    op.drop_table('past_orientation')
