"""Star size (SPEC §6.3 `hfr`, draft E): per sky measurement and per night

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-15 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0054'
down_revision: str | None = '0053'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('sky_sample', sa.Column('hfr', sa.Float(), nullable=True))
    op.add_column('night_stat', sa.Column('median_hfr', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('night_stat', 'median_hfr')
    op.drop_column('sky_sample', 'hfr')
