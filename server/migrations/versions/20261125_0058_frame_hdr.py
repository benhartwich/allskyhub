"""Frames fused with short exposures (SPEC §4.4 `hdr`, §4.13 moon HDR)

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-25 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0058'
down_revision: str | None = '0057'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('frame', sa.Column('hdr', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('frame', 'hdr')
