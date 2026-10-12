"""status samples (charts of exposure, gain, temperatures, disk)

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-14 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0022'
down_revision: str | None = '0021'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'status_sample',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('device_id', sa.String(26), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('mode', sa.String(8), nullable=True),
        sa.Column('exposure_us', sa.BigInteger(), nullable=True),
        sa.Column('gain', sa.Float(), nullable=True),
        sa.Column('sensor_temp_c', sa.Float(), nullable=True),
        sa.Column('cpu_temp_c', sa.Float(), nullable=True),
        sa.Column('disk_free_pct', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['device_id'], ['device.id'],
                                name=op.f('fk_status_sample_device_id_device'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_status_sample')),
    )
    op.create_index('ix_status_sample_device_id_at', 'status_sample', ['device_id', 'at'])


def downgrade() -> None:
    op.drop_table('status_sample')
