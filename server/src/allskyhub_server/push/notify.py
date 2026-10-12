"""When to notify whom (roadmap #6, C2, C3): new detections, cameras that went offline,
aurora while the camera's sky is clear, and the morning summary of a night.

Off unless ``fcm_service_account_file`` is set, and per user only what they switched on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Protocol, cast

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import astro, health, night_summary, space_weather, tonight
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.event_text import EVENT_TITLES
from allskyhub_server.devices.events import storm_key
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.models import Device, EventRecord, PushToken, User
from allskyhub_server.push.fcm import Message

log = logging.getLogger(__name__)

# At most one event notification per camera in this time: aircraft and bursts would spam.
EVENT_QUIET = dt.timedelta(minutes=10)
OFFLINE_AFTER = dt.timedelta(minutes=30)
OFFLINE_CHECK = dt.timedelta(minutes=5)
# Roadmap C2: at most one aurora alarm per camera in this time; the sky measurement must
# be this fresh (status repeats the last one, SPEC §6.3).
AURORA_QUIET = dt.timedelta(hours=6)
SKY_FRESH = dt.timedelta(minutes=20)
CLEAR_BELOW = 0.3
# Roadmap C3: the summary goes out this long after sunrise, and not after local noon.
SUMMARY_AFTER_SUNRISE = dt.timedelta(minutes=30)


class PushSender(Protocol):
    async def send(self, tokens: list[str], message: Message) -> list[str]: ...


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Notifier:
    def __init__(self, maker: async_sessionmaker[AsyncSession], sender: PushSender | None) -> None:
        self._maker = maker
        self._sender = sender
        self._last_event: dict[str, dt.datetime] = {}
        self._storms_notified: set[str] = set()
        # Set by the app: the dome hint (roadmap C12) compares with the cloud forecast.
        self.forecast: ForecastCache | None = None

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
                storm = storm_key(event)
                if storm is not None:
                    # Thunderstorms: once per storm, whatever its length.
                    if storm in self._storms_notified:
                        return
                    self._storms_notified.add(storm)
                else:
                    last = self._last_event.get(device_id)
                    if last is not None and now - last < EVENT_QUIET:
                        return
                    self._last_event[device_id] = now
                title = "Gewitter" if storm else EVENT_TITLES.get(event.kind, event.kind)
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

    async def check_health(
        self, registry: ConnectionRegistry, now: dt.datetime | None = None
    ) -> None:
        """Roadmap C14, one pass: tell owners (who want offline notices) about each new
        problem of a connected camera once, and once when it is gone. Offline itself is
        ``check_offline``."""
        if self._sender is None:
            return
        now = now or _now()
        async with self._maker() as db:
            rows = (
                await db.execute(
                    select(Device, User)
                    .join(User, User.id == Device.owner_id)
                    .where(User.notify_offline.is_(True))
                )
            ).all()
            for device, user in rows:
                online = registry.is_online(device.id)
                if not online:
                    continue  # nothing new can be known; keep what was reported
                found = await health.full_check(db, device, online, now, self.forecast)
                problems = {p.key: p for p in found}
                before = set(device.health_notified or [])
                new = [p for k, p in problems.items() if k not in before]
                gone = before - problems.keys()
                if not new and not gone:
                    continue
                device.health_notified = sorted(problems)
                await db.commit()
                for problem in new:
                    kind = "Problem" if problem.level == "error" else "Hinweis"
                    await self._deliver(
                        db,
                        user.id,
                        Message(
                            title=f"{device.name}: {kind}",
                            body=problem.text,
                            data={"camera": device.id},
                        ),
                    )
                if gone and not problems:
                    await self._deliver(
                        db,
                        user.id,
                        Message(
                            title=f"{device.name} ist wieder in Ordnung",
                            body="Alle Prüfungen sind wieder unauffällig.",
                            data={"camera": device.id},
                        ),
                    )

    async def aurora(self, device_id: str, kp: space_weather.KpCache) -> None:
        """Roadmap C2, after a status with a sky measurement: Kp at or above the rule of
        thumb for the camera's latitude while its own sky is clear and dark."""
        if self._sender is None:
            return
        now = _now()
        try:
            async with self._maker() as db:
                row = (
                    await db.execute(
                        select(Device, User)
                        .join(User, User.id == Device.owner_id)
                        .where(Device.id == device_id, User.notify_aurora.is_(True))
                    )
                ).one_or_none()
                if row is None:
                    return
                device, user = row
                status: dict[str, Any] = device.last_status or {}
                sky = cast(dict[str, Any], status.get("sky") or {})
                settings = cast(dict[str, Any], status.get("settings") or {})
                cloud, lat = sky.get("cloud_cover"), settings.get("latitude")
                if not isinstance(cloud, int | float) or cloud >= CLEAR_BELOW:
                    return
                if not isinstance(lat, int | float):
                    return
                try:
                    measured = dt.datetime.fromisoformat(str(sky.get("at")))
                except ValueError:
                    return
                if now - measured > SKY_FRESH or (device.last_frame or {}).get("mode") != "night":
                    return
                if device.aurora_notified_at and now - device.aurora_notified_at < AURORA_QUIET:
                    return
                forecast = await kp.get()
                need = space_weather.needed_kp(float(lat))
                if forecast is None or forecast.now is None or forecast.now < need:
                    return
                device.aurora_notified_at = now
                await db.commit()
                kp_text = f"{forecast.now:.1f}".replace(".", ",")
                await self._deliver(
                    db,
                    user.id,
                    Message(
                        title=f"Polarlicht möglich über {device.name}",
                        body=f"Kp {kp_text} (hier ab etwa {need:.0f}) und der Himmel ist klar.",
                        data={"camera": device.id},
                    ),
                )
        except Exception:
            log.exception("push: aurora alarm failed")

    async def check_morning(self, now: dt.datetime | None = None) -> None:
        """Roadmap C3, one pass: send each camera's summary of the night once, in the
        morning after sunrise at the camera."""
        if self._sender is None:
            return
        now = now or _now()
        async with self._maker() as db:
            rows = (
                await db.execute(
                    select(Device, User)
                    .join(User, User.id == Device.owner_id)
                    .where(User.notify_summary.is_(True))
                )
            ).all()
            for device, user in rows:
                settings = cast(dict[str, Any], (device.last_status or {}).get("settings") or {})
                lat, lon = settings.get("latitude"), settings.get("longitude")
                if not isinstance(lat, int | float) or not isinstance(lon, int | float):
                    continue
                zone = tonight.zone_for(settings.get("timezone"))
                night_id = night_summary.last_night_id(now, zone)
                if device.summary_night == night_id:
                    continue
                evening = dt.datetime.strptime(night_id, "%Y%m%d").replace(hour=12, tzinfo=zone)
                sunrise = astro.night_after(evening.astimezone(dt.UTC), lat, lon).sunrise
                local = now.astimezone(zone)
                if sunrise is None or now < sunrise + SUMMARY_AFTER_SUNRISE:
                    continue
                device.summary_night = night_id
                await db.commit()
                if local.hour >= 12:
                    continue  # too late to be news (hub was down in the morning)
                summary = await night_summary.summarize(db, device.id, night_id)
                if summary.empty:
                    continue
                data = {"camera": device.id, "night": night_id}
                if summary.best is not None:
                    data["event"] = summary.best.event_id
                await self._deliver(
                    db,
                    user.id,
                    Message(title=f"Die Nacht über {device.name}", body=summary.line(), data=data),
                )

    async def run_offline_checks(self, registry: ConnectionRegistry) -> None:
        while True:
            try:
                await self.check_offline(registry)
            except Exception:
                log.exception("push: offline check failed")
            try:
                await self.check_health(registry)
            except Exception:
                log.exception("push: health check failed")
            try:
                await self.check_morning()
            except Exception:
                log.exception("push: morning summary failed")
            await asyncio.sleep(OFFLINE_CHECK.total_seconds())


def _zone() -> dt.tzinfo:
    from allskyhub_server.web.templating import UI_ZONE

    return UI_ZONE
