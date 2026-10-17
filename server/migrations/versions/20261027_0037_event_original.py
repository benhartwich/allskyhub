"""Original frame of an event (SPEC §6.4, roadmap L28)

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-27 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0037'
down_revision: str | None = '0036'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'event',
        sa.Column('has_original', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column('event', sa.Column('original_w', sa.Integer(), nullable=True))
    op.add_column('event', sa.Column('original_h', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('event', 'original_h')
    op.drop_column('event', 'original_w')
    op.drop_column('event', 'has_original')
