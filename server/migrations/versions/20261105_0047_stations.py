"""Connected cameras, "stations" (SharedSky, SPEC §10)

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-05 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0047'
down_revision: str | None = '0046'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'device',
        sa.Column('kind', sa.String(16), server_default='camera', nullable=False),
    )
    op.add_column('device', sa.Column('software', sa.String(16), nullable=True))
    op.add_column('device', sa.Column('station_secret_hash', sa.LargeBinary(32), nullable=True))
    op.add_column('device', sa.Column('station_secret_enc', sa.LargeBinary(), nullable=True))
    op.add_column(
        'device', sa.Column('terms_accepted_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'device',
        sa.Column('clock', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    for name in (
        'clock', 'terms_accepted_at', 'station_secret_enc', 'station_secret_hash', 'software',
        'kind',
    ):
        op.drop_column('device', name)
