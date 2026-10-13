"""labels with a reason, synced with the camera (SPEC §6.4 Labels)

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-17 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0027'
down_revision: str | None = '0026'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('event', sa.Column('label_reason', sa.String(16), nullable=True))
    op.add_column(
        'event',
        sa.Column('label_pending', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('event', 'label_pending')
    op.drop_column('event', 'label_reason')
