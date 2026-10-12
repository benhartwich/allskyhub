"""own domains for public pages

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-14 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0024'
down_revision: str | None = '0023'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('device', sa.Column('custom_domain', sa.String(253), nullable=True))
    op.add_column('device', sa.Column('custom_domain_state', sa.String(10), nullable=True))
    op.add_column('device', sa.Column('custom_domain_error', sa.String(300), nullable=True))
    op.create_unique_constraint(op.f('uq_device_custom_domain'), 'device', ['custom_domain'])


def downgrade() -> None:
    op.drop_constraint(op.f('uq_device_custom_domain'), 'device', type_='unique')
    op.drop_column('device', 'custom_domain_error')
    op.drop_column('device', 'custom_domain_state')
    op.drop_column('device', 'custom_domain')
