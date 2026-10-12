"""render jobs (timelapse, keogram over nights)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-13 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0019'
down_revision: str | None = '0018'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'render_job',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('device_id', sa.String(26), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fps', sa.Integer(), server_default='25', nullable=False),
        sa.Column('state', sa.String(10), server_default='queued', nullable=False),
        sa.Column('error', sa.String(300), nullable=True),
        sa.Column('frames', sa.Integer(), nullable=True),
        sa.Column('size', sa.BigInteger(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['device_id'], ['device.id'], name=op.f('fk_render_job_device_id_device'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_render_job')),
    )
    op.create_index(op.f('ix_render_job_device_id'), 'render_job', ['device_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_render_job_device_id'), table_name='render_job')
    op.drop_table('render_job')
