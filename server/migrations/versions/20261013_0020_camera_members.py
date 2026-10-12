"""camera sharing

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-13 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0020'
down_revision: str | None = '0019'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'camera_member',
        sa.Column('device_id', sa.String(26), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('role', sa.String(10), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['device_id'], ['device.id'], name=op.f('fk_camera_member_device_id_device'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user_account.id'], name=op.f('fk_camera_member_user_id_user_account'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('device_id', 'user_id', name=op.f('pk_camera_member')),
    )
    op.create_index(op.f('ix_camera_member_user_id'), 'camera_member', ['user_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_camera_member_user_id'), table_name='camera_member')
    op.drop_table('camera_member')
