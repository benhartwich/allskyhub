"""The language of a user's mails ("de" or "en"), for SharedSky's pages in both

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-06 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0049'
down_revision: str | None = '0048'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user_account',
        sa.Column('lang', sa.String(length=2), server_default='de', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('user_account', 'lang')
