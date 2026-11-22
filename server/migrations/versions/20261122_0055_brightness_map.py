"""Sky brightness per sky cell (SPEC §6.3 `brightness_map`, L37): per sky measurement
with its cloud map, and the night map

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-22 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0055'
down_revision: str | None = '0054'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('sky_sample', sa.Column('cloud_map', postgresql.JSONB(), nullable=True))
    op.add_column('sky_sample', sa.Column('brightness_map', postgresql.JSONB(), nullable=True))
    op.add_column(
        'sky_sample', sa.Column('brightness_map_flat', sa.String(length=10), nullable=True)
    )
    op.add_column('night_stat', sa.Column('brightness_map', postgresql.JSONB(), nullable=True))
    op.add_column(
        'night_stat', sa.Column('brightness_map_flat', sa.String(length=10), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('night_stat', 'brightness_map_flat')
    op.drop_column('night_stat', 'brightness_map')
    op.drop_column('sky_sample', 'brightness_map_flat')
    op.drop_column('sky_sample', 'brightness_map')
    op.drop_column('sky_sample', 'cloud_map')
