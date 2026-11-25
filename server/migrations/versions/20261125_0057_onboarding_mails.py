"""Onboarding mails: "one step left" and "how is it going" (Benjamin, 2026-11-25)

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-25 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0057'
down_revision: str | None = '0056'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'device', sa.Column('onboard_mailed_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'device', sa.Column('checkin_mailed_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('device', 'checkin_mailed_at')
    op.drop_column('device', 'onboard_mailed_at')
