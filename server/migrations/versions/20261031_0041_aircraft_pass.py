"""Aircraft by day (roadmap H9, SPEC §7.8 By day)

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-31 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0041'
down_revision: str | None = '0040'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEGREES = (
    'top_alt_deg', 'top_az_deg', 'start_alt_deg', 'start_az_deg', 'end_alt_deg', 'end_az_deg',
)


def upgrade() -> None:
    op.add_column(
        'device',
        sa.Column('day_aircraft', sa.Boolean(), server_default='true', nullable=False),
    )
    op.create_table(
        'aircraft_pass',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('device_id', sa.String(length=26), nullable=False),
        sa.Column('night_id', sa.String(length=8), nullable=False),
        sa.Column('icao', sa.String(length=8), nullable=False),
        sa.Column('callsign', sa.String(length=10), nullable=True),
        sa.Column('registration', sa.String(length=12), nullable=True),
        sa.Column('type_code', sa.String(length=8), nullable=True),
        sa.Column('start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('top_at', sa.DateTime(timezone=True), nullable=False),
        *(sa.Column(name, sa.Float(), nullable=False) for name in DEGREES),
        sa.Column('alt_m', sa.Float(), nullable=True),
        sa.Column('min_km', sa.Float(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['device_id'], ['device.id'], name=op.f('fk_aircraft_pass_device_id_device'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_aircraft_pass')),
    )
    op.create_index(
        'ix_aircraft_pass_device_night', 'aircraft_pass', ['device_id', 'night_id'], unique=False
    )
    op.create_index(
        op.f('ix_aircraft_pass_created_at'), 'aircraft_pass', ['created_at'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_aircraft_pass_created_at'), table_name='aircraft_pass')
    op.drop_index('ix_aircraft_pass_device_night', table_name='aircraft_pass')
    op.drop_table('aircraft_pass')
    op.drop_column('device', 'day_aircraft')
