"""settings schema per device

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-13 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'device',
        sa.Column('settings_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('device', 'settings_schema')
