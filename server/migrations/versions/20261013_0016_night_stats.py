"""night statistics

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-13 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0016'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'night_stat',
        sa.Column('device_id', sa.String(26), nullable=False),
        sa.Column('night_id', sa.String(8), nullable=False),
        sa.Column('clear_hours', sa.Float(), nullable=True),
        sa.Column('best_sqm', sa.Float(), nullable=True),
        sa.Column('meteors', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'showers', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False
        ),
        sa.Column('aurora', sa.Integer(), server_default='0', nullable=False),
        sa.Column('storms', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['device_id'], ['device.id'], name=op.f('fk_night_stat_device_id_device'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('device_id', 'night_id', name=op.f('pk_night_stat')),
    )


def downgrade() -> None:
    op.drop_table('night_stat')
