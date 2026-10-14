"""fireballs: brightness and alarm per meteor pair, and the user's switch

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-21 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0031'
down_revision: str | None = '0030'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('meteor_pair', sa.Column('peak', sa.Float(), nullable=True))
    op.add_column(
        'meteor_pair',
        sa.Column('alerted', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column(
        'user_account',
        sa.Column('notify_fireball', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('user_account', 'notify_fireball')
    op.drop_column('meteor_pair', 'alerted')
    op.drop_column('meteor_pair', 'peak')
