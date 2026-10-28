"""Sightings by eye (fireball, aurora with photo) and alerts at a chosen place (stage 5)

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-02 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0043'
down_revision: str | None = '0042'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLAGS = ('notify_aurora_near', 'notify_fireball_near', 'notify_clear_here')


def _user(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ['user_id'], ['user_account.id'], name=op.f(f'fk_{table}_user_id_user_account'),
        ondelete='CASCADE',
    )


def upgrade() -> None:
    op.add_column('user_account', sa.Column('alert_lat', sa.Float(), nullable=True))
    op.add_column('user_account', sa.Column('alert_lon', sa.Float(), nullable=True))
    op.add_column(
        'user_account',
        sa.Column('alert_radius_km', sa.Integer(), server_default='300', nullable=False),
    )
    for name in FLAGS:
        op.add_column(
            'user_account', sa.Column(name, sa.Boolean(), server_default='false', nullable=False)
        )
    op.add_column(
        'user_account', sa.Column('aurora_near_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'user_account', sa.Column('clear_here_night', sa.String(length=8), nullable=True)
    )
    def created() -> sa.Column[sa.DateTime]:
        return sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        )

    op.create_table(
        'fireball_report',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lon', sa.Float(), nullable=False),
        sa.Column('start_alt', sa.Float(), nullable=True),
        sa.Column('start_az', sa.Float(), nullable=True),
        sa.Column('end_alt', sa.Float(), nullable=True),
        sa.Column('end_az', sa.Float(), nullable=True),
        sa.Column('brightness', sa.String(length=16), nullable=False),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        created(),
        _user('fireball_report'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_fireball_report')),
    )
    op.create_index(op.f('ix_fireball_report_user_id'), 'fireball_report', ['user_id'])
    op.create_index(op.f('ix_fireball_report_seen_at'), 'fireball_report', ['seen_at'])
    op.create_table(
        'aurora_report',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lon', sa.Float(), nullable=False),
        sa.Column('naked_eye', sa.Boolean(), nullable=False),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('photo', sa.String(length=16), server_default='none', nullable=False),
        sa.Column('author', sa.String(length=100), server_default='', nullable=False),
        sa.Column('flags', sa.Integer(), server_default='0', nullable=False),
        created(),
        _user('aurora_report'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_aurora_report')),
    )
    op.create_index(op.f('ix_aurora_report_user_id'), 'aurora_report', ['user_id'])
    op.create_index(op.f('ix_aurora_report_seen_at'), 'aurora_report', ['seen_at'])
    op.create_table(
        'aurora_flag',
        sa.Column('report_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['report_id'], ['aurora_report.id'],
            name=op.f('fk_aurora_flag_report_id_aurora_report'), ondelete='CASCADE',
        ),
        _user('aurora_flag'),
        sa.PrimaryKeyConstraint('report_id', 'user_id', name=op.f('pk_aurora_flag')),
    )


def downgrade() -> None:
    op.drop_table('aurora_flag')
    op.drop_index(op.f('ix_aurora_report_seen_at'), table_name='aurora_report')
    op.drop_index(op.f('ix_aurora_report_user_id'), table_name='aurora_report')
    op.drop_table('aurora_report')
    op.drop_index(op.f('ix_fireball_report_seen_at'), table_name='fireball_report')
    op.drop_index(op.f('ix_fireball_report_user_id'), table_name='fireball_report')
    op.drop_table('fireball_report')
    for name in ('clear_here_night', 'aurora_near_at', *FLAGS, 'alert_radius_km',
                 'alert_lon', 'alert_lat'):
        op.drop_column('user_account', name)
