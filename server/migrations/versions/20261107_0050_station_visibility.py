"""What a station shows publicly (SPEC §10.6 Visibility): named, and for stations that
joined before, a week's notice by mail

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-07 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0050'
down_revision: str | None = '0049'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Benjamin, 2026-11-07: the defaults for every station (named, on the map, a public page,
# no images); stations that joined before get them too, a week after a mail. Until then
# they stay as they were. ``public_from`` far ahead marks "waiting for its mail": the hub
# moves it to a week after a mail that went out (push/notify.py check_station_notice) and
# gives the page then (devices/public.py ``go_public``).
PROFILE = (
    '{"show_coordinates": true, "sections": {"live": false, "loop": false, "archive": false,'
    ' "events": false, "sky": true, "aurora": true, "observatory": false, "aircraft": false}}'
)


def upgrade() -> None:
    op.add_column(
        'device', sa.Column('named', sa.Boolean(), server_default='true', nullable=False)
    )
    op.add_column('device', sa.Column('public_from', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'device', sa.Column('public_mailed_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE device SET"
        " public_profile = COALESCE(public_profile, CAST('" + PROFILE + "' AS jsonb)),"
        " public_from = TIMESTAMPTZ '9999-01-01 00:00+00'"
        " WHERE kind = 'station' AND owner_id IS NOT NULL AND public_slug IS NULL"
    )


def downgrade() -> None:
    for name in ('public_mailed_at', 'public_from', 'named'):
        op.drop_column('device', name)
