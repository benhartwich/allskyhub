"""Periodic clean-up of expired rows, so retention matches the privacy policy."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server.auth.sessions import IDLE_TIMEOUT
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import (
    AppToken,
    DeviceNonce,
    DeviceToken,
    EventRecord,
    Frame,
    Invitation,
    PairingCode,
    Product,
    RateLimit,
    WebSession,
)
from allskyhub_server.settings import Settings

log = logging.getLogger(__name__)

INTERVAL = dt.timedelta(hours=6)
# Privacy policy: invitations that were not accepted are gone 30 days after they expire.
INVITATION_KEEP = dt.timedelta(days=30)
RATE_LIMIT_KEEP = dt.timedelta(days=2)


async def purge(
    db: AsyncSession, store: ImageStore, settings: Settings, now: dt.datetime | None = None
) -> None:
    """Delete everything that has expired. Commits."""
    now = now or dt.datetime.now(dt.UTC)
    await _purge_frames(db, store, settings, now)
    await _purge_products(db, store, settings, now)
    await _purge_events(db, store, settings, now)
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


async def _purge_frames(
    db: AsyncSession, store: ImageStore, settings: Settings, now: dt.datetime
) -> None:
    """Archive retention: full images after ``keep_full_days``, the frame with its thumbnail
    after ``keep_thumb_days`` (counted from arrival at the hub)."""
    full_cutoff = now - dt.timedelta(days=settings.keep_full_days)
    thumb_cutoff = now - dt.timedelta(days=settings.keep_thumb_days)
    old_full = (
        await db.execute(
            select(Frame.device_id, Frame.night_id, Frame.name).where(
                Frame.has_full.is_(True), Frame.created_at < full_cutoff
            )
        )
    ).all()
    for device_id, night_id, name in old_full:
        await asyncio.to_thread(store.delete_frame, device_id, night_id, name, FrameVariant.FULL)
    await db.execute(
        update(Frame)
        .where(Frame.has_full.is_(True), Frame.created_at < full_cutoff)
        .values(has_full=False)
    )
    old = (
        await db.execute(
            select(Frame.device_id, Frame.night_id, Frame.name).where(
                Frame.created_at < thumb_cutoff
            )
        )
    ).all()
    for device_id, night_id, name in old:
        for variant in FrameVariant:
            await asyncio.to_thread(store.delete_frame, device_id, night_id, name, variant)
    await db.execute(delete(Frame).where(Frame.created_at < thumb_cutoff))


async def _purge_products(
    db: AsyncSession, store: ImageStore, settings: Settings, now: dt.datetime
) -> None:
    """Night products: the timelapse (large) after ``keep_full_days`` from its arrival,
    everything of a night after ``keep_thumb_days`` from the announcement."""
    full_cutoff = now - dt.timedelta(days=settings.keep_full_days)
    thumb_cutoff = now - dt.timedelta(days=settings.keep_thumb_days)
    old_videos = (
        await db.execute(
            select(Product.device_id, Product.night_id, Product.name).where(
                Product.kind == "timelapse",
                Product.has_full.is_(True),
                Product.full_at < full_cutoff,
            )
        )
    ).all()
    for device_id, night_id, name in old_videos:
        await asyncio.to_thread(store.delete_product, device_id, night_id, name, FrameVariant.FULL)
    await db.execute(
        update(Product)
        .where(
            Product.kind == "timelapse", Product.has_full.is_(True), Product.full_at < full_cutoff
        )
        .values(has_full=False, full_at=None)
    )
    old = (
        await db.execute(
            select(Product.device_id, Product.night_id, Product.name).where(
                Product.created_at < thumb_cutoff
            )
        )
    ).all()
    for device_id, night_id, name in old:
        for variant in FrameVariant:
            await asyncio.to_thread(store.delete_product, device_id, night_id, name, variant)
    await db.execute(delete(Product).where(Product.created_at < thumb_cutoff))


async def _purge_events(
    db: AsyncSession, store: ImageStore, settings: Settings, now: dt.datetime
) -> None:
    """Events like frames: the full picture after ``keep_full_days``, the event with its
    thumbnail after ``keep_thumb_days`` (from the first report)."""
    full_cutoff = now - dt.timedelta(days=settings.keep_full_days)
    thumb_cutoff = now - dt.timedelta(days=settings.keep_thumb_days)
    old_full = (
        await db.execute(
            select(EventRecord.device_id, EventRecord.night_id, EventRecord.event_id).where(
                EventRecord.has_full.is_(True), EventRecord.created_at < full_cutoff
            )
        )
    ).all()
    for device_id, night_id, event_id in old_full:
        await asyncio.to_thread(
            store.delete_event, device_id, night_id, event_id, FrameVariant.FULL
        )
    await db.execute(
        update(EventRecord)
        .where(EventRecord.has_full.is_(True), EventRecord.created_at < full_cutoff)
        .values(has_full=False)
    )
    old = (
        await db.execute(
            select(EventRecord.device_id, EventRecord.night_id, EventRecord.event_id).where(
                EventRecord.created_at < thumb_cutoff
            )
        )
    ).all()
    for device_id, night_id, event_id in old:
        for variant in FrameVariant:
            await asyncio.to_thread(store.delete_event, device_id, night_id, event_id, variant)
    await db.execute(delete(EventRecord).where(EventRecord.created_at < thumb_cutoff))


async def run_forever(
    maker: async_sessionmaker[AsyncSession], store: ImageStore, settings: Settings
) -> None:
    """Started by the app's lifespan; one pass right away, then every ``INTERVAL``."""
    while True:
        try:
            async with maker() as db:
                await purge(db, store, settings)
        except Exception:
            log.exception("clean-up failed")
        await asyncio.sleep(INTERVAL.total_seconds())
