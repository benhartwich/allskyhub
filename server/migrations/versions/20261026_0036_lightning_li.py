"""Lightning cross-check with Meteosat's Lightning Imager (roadmap H8)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-25 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0036'
down_revision: str | None = '0035'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('event', sa.Column('li_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('event', sa.Column('li_km', sa.Float(), nullable=True))
    op.add_column('event', sa.Column('li_bearing_deg', sa.Float(), nullable=True))
    op.create_table(
        'li_flash',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lon', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_li_flash')),
    )
    op.create_index(op.f('ix_li_flash_at'), 'li_flash', ['at'], unique=False)
    op.create_table(
        'li_granule',
        sa.Column('id', sa.String(length=160), nullable=False),
        sa.Column('start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('flashes', sa.Integer(), nullable=False),
        sa.Column(
            'read_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_li_granule')),
    )
    op.create_index(op.f('ix_li_granule_start'), 'li_granule', ['start'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_li_granule_start'), table_name='li_granule')
    op.drop_table('li_granule')
    op.drop_index(op.f('ix_li_flash_at'), table_name='li_flash')
    op.drop_table('li_flash')
    op.drop_column('event', 'li_bearing_deg')
    op.drop_column('event', 'li_km')
    op.drop_column('event', 'li_checked_at')
