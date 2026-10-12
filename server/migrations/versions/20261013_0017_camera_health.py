"""camera health notifications

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-13 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'device',
        sa.Column(
            'health_notified', postgresql.JSONB(astext_type=sa.Text()), server_default='[]',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('device', 'health_notified')
