"""Lightning cross-check by direction (SPEC §7.10): recheck recent confirmed events

Events of the last two days that a satellite flash in any direction confirmed are checked
again by the next round with the rule that the flash must lie towards the light.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-29 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0039'
down_revision: str | None = '0038'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE event SET li_checked_at = NULL, li_km = NULL, li_bearing_deg = NULL "
        "WHERE kind = 'lightning' AND li_km IS NOT NULL "
        "AND start > now() - interval '47 hours'"
    )


def downgrade() -> None:
    pass  # the next round checks them again either way
