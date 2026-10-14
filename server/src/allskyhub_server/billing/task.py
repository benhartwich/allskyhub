"""Cancels subscriptions whose camera was removed (roadmap N7)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.billing import plus
from allskyhub_server.billing.paddle import Paddle, PaddleError
from allskyhub_server.models import Subscription

log = logging.getLogger(__name__)

INTERVAL = dt.timedelta(minutes=15)


async def cancel_orphans(db: AsyncSession, paddle: Paddle) -> None:
    """Running subscriptions without a camera end with their paid period. Paddle's webhook
    then records the scheduled cancellation, so each is asked once per pass at most."""
    orphans = await db.scalars(
        select(Subscription).where(
            Subscription.device_id.is_(None),
            Subscription.status.in_(plus.RUNNING),
            Subscription.cancel_at.is_(None),
        )
    )
    for sub in orphans:
        try:
            await paddle.cancel(sub.id)
        except PaddleError as exc:
            log.warning("cancelling subscription %s failed: %s", sub.id, exc)


async def run_forever(maker: async_sessionmaker[AsyncSession], paddle: Paddle) -> None:
    while True:
        try:
            async with maker() as db:
                await cancel_orphans(db, paddle)
        except Exception:
            log.exception("billing pass failed")
        await asyncio.sleep(INTERVAL.total_seconds())
