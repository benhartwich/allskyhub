"""A camera's key for the stream view (draft C, /obs/<key>)

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-15 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0053'
down_revision: str | None = '0052'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'obs_token',
        sa.Column('token_hash', sa.LargeBinary(length=32), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('device_id', sa.String(length=26), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['user_account.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['device.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('token_hash'),
        sa.UniqueConstraint('device_id', name='uq_obs_token_device_id'),
    )


def downgrade() -> None:
    op.drop_table('obs_token')
