"""Discord webhook per camera (roadmap H5)

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-16 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0026'
down_revision: str | None = '0025'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('device', sa.Column('discord', postgresql.JSONB(), nullable=True))
    op.add_column('device', sa.Column('discord_night', sa.String(8), nullable=True))
    op.add_column(
        'event',
        sa.Column('discord_posted', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('event', 'discord_posted')
    op.drop_column('device', 'discord_night')
    op.drop_column('device', 'discord')
