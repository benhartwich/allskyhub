"""password reset links, offline e-mail

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-14 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0023'
down_revision: str | None = '0022'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'password_reset',
        sa.Column('token_hash', sa.LargeBinary(32), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user_account.id'],
                                name=op.f('fk_password_reset_user_id_user_account'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_password_reset')),
    )
    op.create_index(op.f('ix_password_reset_user_id'), 'password_reset', ['user_id'])
    op.create_index(op.f('ix_password_reset_expires_at'), 'password_reset', ['expires_at'])
    op.add_column(
        'device', sa.Column('offline_mailed_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('device', 'offline_mailed_at')
    op.drop_table('password_reset')
