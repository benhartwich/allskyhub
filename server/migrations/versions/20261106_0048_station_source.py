"""Stations by their image address, checked by hand (SharedSky, SPEC §10.7)

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-06 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0048'
down_revision: str | None = '0047'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'station_source',
        sa.Column('device_id', sa.String(26), nullable=False),
        sa.Column('url', sa.String(2048), nullable=False),
        sa.Column('state', sa.String(16), server_default='pending', nullable=False),
        sa.Column('proof_text', sa.String(2000), server_default='', nullable=False),
        sa.Column('proof_image', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewed_by', sa.String(254), nullable=True),
        sa.Column('reason', sa.String(500), server_default='', nullable=False),
        sa.Column('etag', sa.String(256), nullable=True),
        sa.Column('last_modified', sa.String(64), nullable=True),
        sa.Column('content_hash', sa.LargeBinary(32), nullable=True),
        sa.Column('next_fetch_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('interval_s', sa.Integer(), server_default='30', nullable=False),
        sa.Column('changes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]',
                  nullable=False),
        sa.Column('errors', sa.Integer(), server_default='0', nullable=False),
        sa.Column('errors_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(200), server_default='', nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['device.id'],
                                name=op.f('fk_station_source_device_id_device'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('device_id', name=op.f('pk_station_source')),
    )  # fmt: skip
    op.create_index(op.f('ix_station_source_state'), 'station_source', ['state'])


def downgrade() -> None:
    op.drop_index(op.f('ix_station_source_state'), table_name='station_source')
    op.drop_table('station_source')
