"""Reporter accounts and sky-brightness reports (app "Himmelshelligkeit melden")

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-01 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0042'
down_revision: str | None = '0041'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user_account',
        sa.Column('kind', sa.String(length=16), server_default='owner', nullable=False),
    )
    op.create_table(
        'email_confirmation',
        sa.Column('token_hash', sa.LargeBinary(length=32), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user_account.id'],
            name=op.f('fk_email_confirmation_user_id_user_account'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_email_confirmation')),
    )
    op.create_index(
        op.f('ix_email_confirmation_user_id'), 'email_confirmation', ['user_id'], unique=False
    )
    op.create_table(
        'sky_report',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lon', sa.Float(), nullable=False),
        sa.Column('constellation', sa.String(length=3), nullable=False),
        sa.Column('limit_mag', sa.Float(), nullable=False),
        sa.Column('sqm', sa.Float(), nullable=False),
        sa.Column('clouds', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user_account.id'], name=op.f('fk_sky_report_user_id_user_account'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sky_report')),
    )
    op.create_index(op.f('ix_sky_report_user_id'), 'sky_report', ['user_id'], unique=False)
    op.create_index(op.f('ix_sky_report_at'), 'sky_report', ['at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_sky_report_at'), table_name='sky_report')
    op.drop_index(op.f('ix_sky_report_user_id'), table_name='sky_report')
    op.drop_table('sky_report')
    op.drop_index(op.f('ix_email_confirmation_user_id'), table_name='email_confirmation')
    op.drop_table('email_confirmation')
    op.drop_column('user_account', 'kind')
