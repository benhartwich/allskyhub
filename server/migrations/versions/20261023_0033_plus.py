"""Plus: the longer archive per camera (roadmap N7)

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-23 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0033'
down_revision: str | None = '0032'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('device', sa.Column('plus_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'device',
        sa.Column('plus_gift', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('device', 'plus_gift')
    op.drop_column('device', 'plus_until')
