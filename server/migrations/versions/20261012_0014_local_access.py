"""local access (offline code verifier)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-12 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('device', sa.Column('local_access_verifier', sa.String(512), nullable=True))
    op.add_column(
        'device',
        sa.Column('local_access_pending', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column(
        'device', sa.Column('local_access_set_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('device', 'local_access_set_at')
    op.drop_column('device', 'local_access_pending')
    op.drop_column('device', 'local_access_verifier')
