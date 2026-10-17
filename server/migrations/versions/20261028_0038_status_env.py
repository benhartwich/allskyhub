"""Weather sensor and dew heater in the status samples (SPEC §7.11, roadmap L30)

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-28 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0038'
down_revision: str | None = '0037'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ('air_temp_c', 'humidity_pct', 'dewpoint_c', 'pressure_hpa')


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column('status_sample', sa.Column(name, sa.Float(), nullable=True))
    op.add_column('status_sample', sa.Column('heater_on', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('status_sample', 'heater_on')
    for name in reversed(COLUMNS):
        op.drop_column('status_sample', name)
