"""Plus as a gift for a term (roadmap N7, Benjamin 2026-11-25: a year for feedback)

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-25 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0056'
down_revision: str | None = '0055'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'device', sa.Column('plus_gift_until', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('device', 'plus_gift_until')
