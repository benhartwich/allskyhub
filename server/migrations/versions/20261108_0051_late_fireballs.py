"""Fireballs sent later (SPEC §10.8): the uploads and the mark on their pairs

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-08 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0051'
down_revision: str | None = '0050'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'late_fireball',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('device_id', sa.String(length=26), nullable=False),
        sa.Column('taken_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('exposure_s', sa.Float(), nullable=False),
        sa.Column('track_px', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=16), server_default='track', nullable=False),
        sa.Column('reason', sa.String(length=32), nullable=True),
        sa.Column(
            'check',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='{}',
            nullable=False,
        ),
        sa.Column('event_id', sa.String(length=64), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['device_id'], ['device.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_late_fireball_device_id', 'late_fireball', ['device_id'])
    op.add_column(
        'meteor_pair', sa.Column('late', sa.Boolean(), server_default='false', nullable=False)
    )


def downgrade() -> None:
    op.drop_column('meteor_pair', 'late')
    op.drop_index('ix_late_fireball_device_id', table_name='late_fireball')
    op.drop_table('late_fireball')
