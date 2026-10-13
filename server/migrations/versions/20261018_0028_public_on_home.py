"""public cameras on the start page, only when the owner opts in

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-18 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0028'
down_revision: str | None = '0027'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'device',
        sa.Column('public_on_home', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('device', 'public_on_home')
