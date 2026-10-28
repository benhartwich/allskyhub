"""Periodic clean-up of expired rows, so retention matches the privacy policy."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

from allskyhub_protocol import EventVariant, FrameVariant
from allskyhub_server.auth import access_requests, password_reset, reporters
from allskyhub_server.auth.accounts import DEFAULT_EVENT_KEEP_DAYS
from allskyhub_server.auth.sessions import IDLE_TIMEOUT
from allskyhub_server.devices import camera_map, renders, sightings, stats
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import (
    AircraftPass,
    AppToken,
    AuroraReport,
    Device,
    DeviceNonce,
    DeviceToken,
    EventRecord,
    FireballReport,
    Frame,
    Invitation,
    PairingCode,
    Product,
    RateLimit,
    SkyReport,
    SkySample,
    StatusSample,
    User,
    WebSession,
)
from allskyhub_server.settings import Settings

log = logging.getLogger(__name__)

INTERVAL = dt.timedelta(hours=6)
# Privacy policy: invitations that were not accepted are gone 30 days after they expire.
INVITATION_KEEP = dt.timedelta(days=30)
RATE_LIMIT_KEEP = dt.timedelta(days=2)
# Roadmap N7: after Plus ends the longer archive stays this long, so a late renewal or a
# changed card does not cost the pictures.
PLUS_GRACE = dt.timedelta(days=30)


def _expired(
    created: InstrumentedAttribute[dt.datetime] | InstrumentedAttribute[dt.datetime | None],
    device_id: InstrumentedAttribute[str],
    now: dt.datetime,
    days: int,
    plus_days: int,
) -> ColumnElement[bool]:
    """Older than ``days``, or than ``plus_days`` for a camera with Plus (or in its grace)."""
    plus = select(Device.id).where(
        or_(Device.plus_gift.is_(True), Device.plus_until > now - PLUS_GRACE)
    )
    return or_(
        and_(device_id.not_in(plus), created < now - dt.timedelta(days=days)),
        created < now - dt.timedelta(days=max(days, plus_days)),
    )


async def purge(
    db: AsyncSession, store: ImageStore, settings: Settings, now: dt.datetime | None = None
) -> None:
    """Delete everything that has expired. Commits."""
    now = now or dt.datetime.now(dt.UTC)
    # Roadmap C7: keep each night in numbers before its samples and events expire.
    await stats.refresh(db, now)
    await renders.purge(db, store, settings, now)
    await password_reset.purge(db, now)
    await reporters.purge(db, now)
    # Reports by eye count on the map for a year (devices/camera_map.py), then go.
    await db.execute(
        delete(SkyReport).where(SkyReport.at < now - dt.timedelta(days=camera_map.REPORT_DAYS))
    )
    # Sightings by eye (stage 5) go after a year too, aurora photos with them.
    year_ago = now - dt.timedelta(days=365)
    await db.execute(delete(FireballReport).where(FireballReport.seen_at < year_ago))
    old = list(await db.scalars(select(AuroraReport.id).where(AuroraReport.seen_at < year_ago)))
    for report_id in old:
        await asyncio.to_thread(sightings.delete_photo, settings.data_dir / "sightings", report_id)
    if old:
        await db.execute(delete(AuroraReport).where(AuroraReport.id.in_(old)))
    await db.flush()
    await sightings.sweep(db, settings.data_dir / "sightings")
    await access_requests.purge(db, now)
    await _purge_frames(db, store, settings, now)
    # Roadmap H9: the day's aircraft go with the night's other data.
    await db.execute(
        delete(AircraftPass).where(
            _expired(
                AircraftPass.created_at, AircraftPass.device_id, now, settings.keep_thumb_days,
                settings.keep_plus_thumb_days,
            )
        )
    )  # fmt: skip
    await _purge_products(db, store, settings, now)
    await _purge_events(db, store, settings, now)
    await db.execute(
        delete(SkySample).where(
            SkySample.created_at < now - dt.timedelta(days=settings.keep_thumb_days)
        )
    )
    await db.execute(
        delete(StatusSample).where(
            StatusSample.at < now - dt.timedelta(days=settings.keep_thumb_days)
        )
    )
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
    after ``keep_thumb_days`` (counted from arrival at the hub); with Plus the longer
    ``keep_plus_*`` days."""
    old_full_frame = _expired(
        Frame.created_at, Frame.device_id, now, settings.keep_full_days,
        settings.keep_plus_full_days,
    )  # fmt: skip
    old_frame = _expired(
        Frame.created_at, Frame.device_id, now, settings.keep_thumb_days,
        settings.keep_plus_thumb_days,
    )  # fmt: skip
    old_full = (
        await db.execute(
            select(Frame.device_id, Frame.night_id, Frame.name).where(
                Frame.has_full.is_(True), old_full_frame
            )
        )
    ).all()
    for device_id, night_id, name in old_full:
        await asyncio.to_thread(store.delete_frame, device_id, night_id, name, FrameVariant.FULL)
    await db.execute(
        update(Frame).where(Frame.has_full.is_(True), old_full_frame).values(has_full=False)
    )
    old = (
        await db.execute(select(Frame.device_id, Frame.night_id, Frame.name).where(old_frame))
    ).all()
    for device_id, night_id, name in old:
        for variant in FrameVariant:
            await asyncio.to_thread(store.delete_frame, device_id, night_id, name, variant)
    await db.execute(delete(Frame).where(old_frame))


async def _purge_products(
    db: AsyncSession, store: ImageStore, settings: Settings, now: dt.datetime
) -> None:
    """Night products: videos (the timelapses, large) after ``keep_full_days`` from arrival,
    everything of a night after ``keep_thumb_days`` from the announcement; with Plus the
    longer ``keep_plus_*`` days."""
    old_video = and_(
        Product.content_type == "video/mp4",
        Product.has_full.is_(True),
        _expired(
            Product.full_at, Product.device_id, now, settings.keep_full_days,
            settings.keep_plus_full_days,
        ),
    )  # fmt: skip
    old_product = _expired(
        Product.created_at, Product.device_id, now, settings.keep_thumb_days,
        settings.keep_plus_thumb_days,
    )  # fmt: skip
    old_videos = (
        await db.execute(select(Product.device_id, Product.night_id, Product.name).where(old_video))
    ).all()
    for device_id, night_id, name in old_videos:
        await asyncio.to_thread(store.delete_product, device_id, night_id, name, FrameVariant.FULL)
    await db.execute(update(Product).where(old_video).values(has_full=False, full_at=None))
    old = (
        await db.execute(
            select(Product.device_id, Product.night_id, Product.name).where(old_product)
        )
    ).all()
    for device_id, night_id, name in old:
        for variant in FrameVariant:
            await asyncio.to_thread(store.delete_product, device_id, night_id, name, variant)
    await db.execute(delete(Product).where(old_product))


async def _purge_events(
    db: AsyncSession, store: ImageStore, settings: Settings, now: dt.datetime
) -> None:
    """Detections, per account (``User.event_keep_days``): the event with its pictures after
    that many days from the first report. With the default (30 days) the full picture goes
    after ``keep_full_days`` like a frame's; a longer choice keeps it with the event."""
    keep = func.make_interval(0, 0, 0, User.event_keep_days)
    owned = (
        select(EventRecord.device_id, EventRecord.night_id, EventRecord.event_id)
        .join(Device, Device.id == EventRecord.device_id)
        .join(User, User.id == Device.owner_id)
    )
    full_cutoff = now - dt.timedelta(days=settings.keep_full_days)
    old_full = (
        await db.execute(
            owned.where(
                EventRecord.has_full.is_(True) | EventRecord.has_original.is_(True),
                EventRecord.created_at < full_cutoff,
                User.event_keep_days <= DEFAULT_EVENT_KEEP_DAYS,
            )
        )
    ).all()
    for device_id, night_id, event_id in old_full:
        # The original frame is kept with the full picture (SPEC §6.5).
        for variant in (EventVariant.FULL, EventVariant.ORIGINAL):
            await asyncio.to_thread(store.delete_event, device_id, night_id, event_id, variant)
        await db.execute(
            update(EventRecord)
            .where(EventRecord.device_id == device_id, EventRecord.event_id == event_id)
            .values(has_full=False, has_original=False)
        )
    expired = (await db.execute(owned.where(EventRecord.created_at < now - keep))).all()
    for device_id, night_id, event_id in expired:
        for variant in EventVariant:
            await asyncio.to_thread(store.delete_event, device_id, night_id, event_id, variant)
        await db.execute(
            delete(EventRecord).where(
                EventRecord.device_id == device_id, EventRecord.event_id == event_id
            )
        )


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
