"""Access requests from the public page (/zugang)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-25 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0035'
down_revision: str | None = '0034'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'access_request',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('place', sa.String(length=100), server_default='', nullable=False),
        sa.Column('stage', sa.String(length=16), nullable=False),
        sa.Column('message', sa.String(length=1000), server_default='', nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('decision', sa.String(length=16), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_access_request')),
    )
    op.create_index(
        op.f('ix_access_request_email'), 'access_request', ['email'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_access_request_email'), table_name='access_request')
    op.drop_table('access_request')
