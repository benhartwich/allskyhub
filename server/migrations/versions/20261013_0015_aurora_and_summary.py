"""aurora alarm and morning summary

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-13 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ('notify_aurora', 'notify_summary'):
        op.add_column(
            'user_account', sa.Column(name, sa.Boolean(), server_default='false', nullable=False)
        )
    op.add_column(
        'device', sa.Column('aurora_notified_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('device', sa.Column('summary_night', sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column('device', 'summary_night')
    op.drop_column('device', 'aurora_notified_at')
    op.drop_column('user_account', 'notify_summary')
    op.drop_column('user_account', 'notify_aurora')
