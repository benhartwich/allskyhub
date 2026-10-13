"""automatic labels (aircraft, SPEC §6.4 Labels, §7.8)

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-19 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0029'
down_revision: str | None = '0028'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'event', sa.Column('label_auto', sa.Boolean(), server_default='false', nullable=False)
    )
    op.add_column('event', sa.Column('label_detail', sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column('event', 'label_detail')
    op.drop_column('event', 'label_auto')
