"""Plus: trial month and Paddle subscriptions (roadmap N7)

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0034'
down_revision: str | None = '0033'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'device', sa.Column('plus_trial_until', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'user_account', sa.Column('plus_trial_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        'subscription',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('device_id', sa.String(length=26), nullable=True),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('customer_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('price_id', sa.String(length=64), server_default='', nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('event_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['device_id'], ['device.id'], name='fk_subscription_device_id_device',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user_account.id'], name='fk_subscription_user_id_user_account',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_subscription'),
    )
    op.create_index('ix_subscription_device_id', 'subscription', ['device_id'])
    op.create_index('ix_subscription_user_id', 'subscription', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_subscription_user_id', table_name='subscription')
    op.drop_index('ix_subscription_device_id', table_name='subscription')
    op.drop_table('subscription')
    op.drop_column('user_account', 'plus_trial_at')
    op.drop_column('device', 'plus_trial_until')
