"""notifications by e-mail, evening clear-sky notice

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-15 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0025'
down_revision: str | None = '0024'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user_account',
        sa.Column('notify_by_mail', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column(
        'user_account',
        sa.Column('notify_tonight', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column('device', sa.Column('tonight_night', sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column('device', 'tonight_night')
    op.drop_column('user_account', 'notify_tonight')
    op.drop_column('user_account', 'notify_by_mail')
