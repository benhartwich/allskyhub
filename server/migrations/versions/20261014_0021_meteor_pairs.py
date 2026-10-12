"""meteor pairs (triangulation)

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-14 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0021'
down_revision: str | None = '0020'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'meteor_pair',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('device_a', sa.String(26), nullable=False),
        sa.Column('event_a', sa.String(64), nullable=False),
        sa.Column('device_b', sa.String(26), nullable=False),
        sa.Column('event_b', sa.String(64), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        *[sa.Column(name, sa.Float(), nullable=False) for name in (
            'high_lat', 'high_lon', 'high_km', 'low_lat', 'low_lon', 'low_km', 'length_km',
            'convergence_deg')],
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['device_a'], ['device.id'],
                                name=op.f('fk_meteor_pair_device_a_device'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_b'], ['device.id'],
                                name=op.f('fk_meteor_pair_device_b_device'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_meteor_pair')),
        sa.UniqueConstraint('device_a', 'event_a', 'device_b', 'event_b',
                            name=op.f('uq_meteor_pair_device_a_event_a_device_b_event_b')),
    )
    op.create_index(op.f('ix_meteor_pair_device_a'), 'meteor_pair', ['device_a'])
    op.create_index(op.f('ix_meteor_pair_device_b'), 'meteor_pair', ['device_b'])


def downgrade() -> None:
    op.drop_table('meteor_pair')
