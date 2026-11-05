"""Meteors the Global Meteor Network measured too (fireball package D)

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-05 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0045'
down_revision: str | None = '0044'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLOATS = ('v_avg_kms', 'duration_s', 'peak_mag', 'mass_kg', 'a_au', 'e', 'i_deg', 'q_au')


def upgrade() -> None:
    op.create_table(
        'gmn_meteor',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('device_id', sa.String(26), nullable=False),
        sa.Column('event_id', sa.String(length=64), nullable=False),
        sa.Column('gmn_id', sa.String(length=40), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('shower', sa.String(length=8), nullable=True),
        sa.Column('v_init_kms', sa.Float(), nullable=False),
        *(sa.Column(c, sa.Float(), nullable=False)
          for c in ('beg_lat', 'beg_lon', 'beg_km', 'end_lat', 'end_lon', 'end_km')),
        *(sa.Column(c, sa.Float(), nullable=True) for c in FLOATS),
        sa.Column('stations', sa.Integer(), nullable=False),
        sa.Column('offset_deg', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['device.id'],
                                name=op.f('fk_gmn_meteor_device_id_device'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_gmn_meteor')),
        sa.UniqueConstraint('device_id', 'event_id',
                            name=op.f('uq_gmn_meteor_device_id_event_id')),
    )  # fmt: skip
    op.create_index(op.f('ix_gmn_meteor_device_id'), 'gmn_meteor', ['device_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_gmn_meteor_device_id'), table_name='gmn_meteor')
    op.drop_table('gmn_meteor')
