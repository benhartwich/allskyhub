"""Fireballs: all cameras of a meteor solved together, with the error of its ends

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-05 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0044'
down_revision: str | None = '0043'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'meteor_pair',
        sa.Column('cameras', postgresql.JSONB(astext_type=sa.Text()), server_default='[]',
                  nullable=False),
    )  # fmt: skip
    op.add_column('meteor_pair', sa.Column('high_err_km', sa.Float(), nullable=True))
    op.add_column('meteor_pair', sa.Column('low_err_km', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('meteor_pair', 'low_err_km')
    op.drop_column('meteor_pair', 'high_err_km')
    op.drop_column('meteor_pair', 'cameras')
