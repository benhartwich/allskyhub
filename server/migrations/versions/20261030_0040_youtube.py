"""YouTube upload of the night's timelapse (roadmap H5)

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-30 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0040'
down_revision: str | None = '0039'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('device', sa.Column('youtube', postgresql.JSONB(), nullable=True))
    op.add_column('product', sa.Column('youtube_id', sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column('product', 'youtube_id')
    op.drop_column('device', 'youtube')
