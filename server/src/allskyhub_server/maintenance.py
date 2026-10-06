"""Periodic clean-up of expired rows, so retention matches the privacy policy."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.auth.sessions import IDLE_TIMEOUT
from allskyhub_server.models import (
    AppToken,
    DeviceNonce,
    DeviceToken,
    Invitation,
    PairingCode,
    RateLimit,
    WebSession,
)

log = logging.getLogger(__name__)

INTERVAL = dt.timedelta(hours=6)
# Privacy policy: invitations that were not accepted are gone 30 days after they expire.
INVITATION_KEEP = dt.timedelta(days=30)
RATE_LIMIT_KEEP = dt.timedelta(days=2)


async def purge(db: AsyncSession, now: dt.datetime | None = None) -> None:
    """Delete everything that has expired. Commits."""
    now = now or dt.datetime.now(dt.UTC)
    await db.execute(
        delete(Invitation).where(
            Invitation.used_at.is_(None), Invitation.expires_at < now - INVITATION_KEEP
        )
    )
    # Accepted invitations are not needed once the account exists.
    await db.execute(delete(Invitation).where(Invitation.used_at.is_not(None)))
    await db.execute(
        delete(WebSession).where(
            (WebSession.expires_at <= now) | (WebSession.last_seen_at + IDLE_TIMEOUT <= now)
        )
    )
    await db.execute(delete(AppToken).where(AppToken.expires_at <= now))
    await db.execute(delete(DeviceToken).where(DeviceToken.expires_at <= now))
    await db.execute(delete(DeviceNonce).where(DeviceNonce.expires_at <= now))
    await db.execute(
        delete(PairingCode).where(PairingCode.claimed_at.is_(None), PairingCode.expires_at <= now)
    )
    await db.execute(delete(RateLimit).where(RateLimit.window_start < now - RATE_LIMIT_KEEP))
    await db.commit()


async def run_forever(maker: async_sessionmaker[AsyncSession]) -> None:
    """Started by the app's lifespan; one pass right away, then every ``INTERVAL``."""
    while True:
        try:
            async with maker() as db:
                await purge(db)
        except Exception:
            log.exception("clean-up failed")
        await asyncio.sleep(INTERVAL.total_seconds())
