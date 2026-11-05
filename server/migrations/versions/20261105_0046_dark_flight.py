"""Fireballs: winds aloft and the strewn field of the dark flight (package C)

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-05 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0046'
down_revision: str | None = '0045'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ('wind', 'strewn'):
        op.add_column(
            'meteor_pair',
            sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    op.drop_column('meteor_pair', 'strewn')
    op.drop_column('meteor_pair', 'wind')
