"""When to notify whom (roadmap #6): new detections and cameras that went offline.

Off unless ``fcm_service_account_file`` is set, and per user only what they switched on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.event_text import EVENT_TITLES
from allskyhub_server.models import Device, EventRecord, PushToken, User
from allskyhub_server.push.fcm import Message

log = logging.getLogger(__name__)

# At most one event notification per camera in this time: aircraft and bursts would spam.
EVENT_QUIET = dt.timedelta(minutes=10)
OFFLINE_AFTER = dt.timedelta(minutes=30)
OFFLINE_CHECK = dt.timedelta(minutes=5)


class PushSender(Protocol):
    async def send(self, tokens: list[str], message: Message) -> list[str]: ...


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Notifier:
    def __init__(self, maker: async_sessionmaker[AsyncSession], sender: PushSender | None) -> None:
        self._maker = maker
        self._sender = sender
        self._last_event: dict[str, dt.datetime] = {}

    @property
    def enabled(self) -> bool:
        return self._sender is not None

    async def _deliver(self, db: AsyncSession, user_id: object, message: Message) -> None:
        if self._sender is None:
            return
        tokens = list(await db.scalars(select(PushToken.token).where(PushToken.user_id == user_id)))
        dead = await self._sender.send(tokens, message)
        if dead:
            await db.execute(delete(PushToken).where(PushToken.token.in_(dead)))
            await db.commit()

    async def event(self, device_id: str, event_id: str) -> None:
        """A new detection (not a resent one). Called in its own task."""
        if self._sender is None:
            return
        now = _now()
        last = self._last_event.get(device_id)
        if last is not None and now - last < EVENT_QUIET:
            return
        try:
            async with self._maker() as db:
                row = (
                    await db.execute(
                        select(Device, User, EventRecord)
                        .join(User, User.id == Device.owner_id)
                        .join(EventRecord, EventRecord.device_id == Device.id)
                        .where(Device.id == device_id, EventRecord.event_id == event_id)
                    )
                ).one_or_none()
                if row is None:
                    return
                device, user, event = row
                if not user.notify_events or event.label == "false_positive":
                    return
                self._last_event[device_id] = now
                title = EVENT_TITLES.get(event.kind, event.kind)
                await self._deliver(
                    db,
                    user.id,
                    Message(
                        title=f"{title} über {device.name}",
                        body=f"Erkannt um {event.start.astimezone(_zone()).strftime('%H:%M')} Uhr.",
                        data={
                            "camera": device.id,
                            "night": event.night_id,
                            "event": event.event_id,
                        },
                    ),
                )
        except Exception:
            log.exception("push: event notification failed")

    async def check_offline(self, registry: ConnectionRegistry) -> None:
        """One pass: cameras silent for ``OFFLINE_AFTER`` whose owner wants to know."""
        if self._sender is None:
            return
        cutoff = _now() - OFFLINE_AFTER
        async with self._maker() as db:
            rows = (
                await db.execute(
                    select(Device, User)
                    .join(User, User.id == Device.owner_id)
                    .where(
                        User.notify_offline.is_(True),
                        Device.last_seen_at < cutoff,
                        Device.offline_notified_at.is_(None),
                    )
                )
            ).all()
            for device, user in rows:
                if registry.is_online(device.id):
                    continue
                await db.execute(
                    update(Device).where(Device.id == device.id).values(offline_notified_at=_now())
                )
                await db.commit()
                await self._deliver(
                    db,
                    user.id,
                    Message(
                        title=f"{device.name} ist offline",
                        body="Die Kamera hat sich seit 30 Minuten nicht gemeldet.",
                        data={"camera": device.id},
                    ),
                )

    async def run_offline_checks(self, registry: ConnectionRegistry) -> None:
        while True:
            try:
                await self.check_offline(registry)
            except Exception:
                log.exception("push: offline check failed")
            await asyncio.sleep(OFFLINE_CHECK.total_seconds())


def _zone() -> dt.tzinfo:
    from allskyhub_server.web.templating import UI_ZONE

    return UI_ZONE
