"""When to notify whom (roadmap #6, C1, C2, C3, #15): new detections, cameras that went
offline or have a problem, aurora while the camera's sky is clear, the morning summary of
a night and, in the evening, a clear night ahead.

Push needs ``fcm_service_account_file``; e-mail needs ``smtp_host`` and the user's
``notify_by_mail``. Single detections go by push only (a meteor night would flood the
inbox; they are in the morning summary). Per user only what they switched on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Protocol, cast

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import (
    astro,
    events,
    health,
    night_summary,
    space_weather,
    tonight,
)
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.discord import Discord
from allskyhub_server.devices.event_text import EVENT_TITLES
from allskyhub_server.devices.events import storm_key
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.models import Device, EventRecord, PushToken, User
from allskyhub_server.push.fcm import Message

log = logging.getLogger(__name__)

# At most one event notification per camera in this time: aircraft and bursts would spam.
EVENT_QUIET = dt.timedelta(minutes=10)
EVENT_TOO_OLD = dt.timedelta(hours=6)
OFFLINE_AFTER = dt.timedelta(minutes=30)
OFFLINE_CHECK = dt.timedelta(minutes=5)
# Roadmap #15: a long outage also by e-mail, once.
OFFLINE_MAIL_AFTER = dt.timedelta(hours=6)
# Roadmap C2: at most one aurora alarm per camera in this time; the sky measurement must
# be this fresh (status repeats the last one, SPEC §6.3).
AURORA_QUIET = dt.timedelta(hours=6)
SKY_FRESH = dt.timedelta(minutes=20)
CLEAR_BELOW = 0.3
# Roadmap C3: the summary goes out this long after sunrise, and not after local noon.
SUMMARY_AFTER_SUNRISE = dt.timedelta(minutes=30)
# Roadmap C1: the evening notice goes out from this long before sunset until dusk, when
# the forecast has a good stretch of at least this length.
TONIGHT_BEFORE_SUNSET = dt.timedelta(hours=1)
TONIGHT_MIN_CLEAR = dt.timedelta(hours=2)


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
        # Set by the app: e-mail (roadmap #15, C14) and the hub's address for links in it.
        self.mailer: Mailer | None = None
        self.base_url = ""
        # Set by the app: the owner's Discord channel (roadmap H5).
        self.discord: Discord | None = None

    @property
    def enabled(self) -> bool:
        """Push is possible (the app shows its switches only then)."""
        return self._sender is not None

    @property
    def _mail(self) -> bool:
        return self.mailer is not None and self.mailer.enabled

    @property
    def _active(self) -> bool:
        return self._sender is not None or self._mail

    async def _deliver(
        self, db: AsyncSession, user: User, message: Message, *, by_mail: bool = True
    ) -> None:
        """Push to the user's phones; also e-mail if allowed for this kind and wanted."""
        if self._sender is not None:
            tokens = list(
                await db.scalars(select(PushToken.token).where(PushToken.user_id == user.id))
            )
            dead = await self._sender.send(tokens, message)
            if dead:
                await db.execute(delete(PushToken).where(PushToken.token.in_(dead)))
                await db.commit()
        if by_mail and user.notify_by_mail and self.mailer is not None and self._mail:
            await self.mailer.send(self._as_mail(user.email, message))

    def _as_mail(self, to: str, message: Message) -> Mail:
        camera, night = message.data.get("camera"), message.data.get("night")
        path = f"/cameras/{camera}" if camera else "/"
        if camera and night:
            path += f"/nights/{night}"
        base = self.base_url.rstrip("/")
        return Mail(
            to=to,
            subject=f"allskyhub: {message.title}",
            body=(
                f"{message.title}\n\n{message.body}\n\n{base}{path}\n\n"
                f"Welche Nachrichten per E-Mail kommen, stellst du unter {base}/account ein.\n"
            ),
        )

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
                if not user.notify_events or events.dismissed(event):
                    return
                # Old detections (an import, a camera that was offline for long) are news
                # no more: no push for them.
                if now - event.start > EVENT_TOO_OLD or (event.data or {}).get("imported"):
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
                    user,
                    Message(
                        title=f"{title} über {device.name}",
                        body=f"Erkannt um {event.start.astimezone(_zone()).strftime('%H:%M')} Uhr.",
                        data={
                            "camera": device.id,
                            "night": event.night_id,
                            "event": event.event_id,
                        },
                    ),
                    by_mail=False,
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
                # By e-mail only after hours (check_offline_mail): short outages are common.
                await self._deliver(
                    db,
                    user,
                    Message(
                        title=f"{device.name} ist offline",
                        body="Die Kamera hat sich seit 30 Minuten nicht gemeldet.",
                        data={"camera": device.id},
                    ),
                    by_mail=False,
                )

    async def check_health(
        self, registry: ConnectionRegistry, now: dt.datetime | None = None
    ) -> None:
        """Roadmap C14, one pass: tell owners (who want offline notices) about each new
        problem of a connected camera once, and once when it is gone. Offline itself is
        ``check_offline``."""
        if not self._active:
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
                        user,
                        Message(
                            title=f"{device.name}: {kind}",
                            body=problem.text,
                            data={"camera": device.id},
                        ),
                    )
                if gone and not problems:
                    await self._deliver(
                        db,
                        user,
                        Message(
                            title=f"{device.name} ist wieder in Ordnung",
                            body="Alle Prüfungen sind wieder unauffällig.",
                            data={"camera": device.id},
                        ),
                    )

    async def unsafe(self, device_id: str) -> None:
        """Roadmap C15: the camera's safety monitor turned unsafe while N.I.N.A. is
        connected; owners with problem notices get it once per change."""
        if not self._active:
            return
        try:
            async with self._maker() as db:
                row = (
                    await db.execute(
                        select(Device, User)
                        .join(User, User.id == Device.owner_id)
                        .where(Device.id == device_id, User.notify_offline.is_(True))
                    )
                ).one_or_none()
                if row is None:
                    return
                device, user = row
                await self._deliver(
                    db,
                    user,
                    Message(
                        title=f"{device.name}: Himmel unsicher",
                        body="Wolken, Regen oder Gewitter: Das Teleskop sollte jetzt pausieren.",
                        data={"camera": device.id},
                    ),
                )
        except Exception:
            log.exception("push: unsafe notice failed")

    async def aurora(self, device_id: str, kp: space_weather.KpCache) -> None:
        """Roadmap C2, after a status with a sky measurement: Kp at or above the rule of
        thumb for the camera's latitude while its own sky is clear and dark."""
        if not self._active:
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
                    user,
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
        if not self._active:
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
                    user,
                    Message(title=f"Die Nacht über {device.name}", body=summary.line(), data=data),
                )

    async def check_tonight(self, now: dt.datetime | None = None) -> None:
        """Roadmap C1, one pass: in the evening (from an hour before sunset until dusk) tell
        owners once per night and camera when the forecast has a clear, dark stretch of
        at least ``TONIGHT_MIN_CLEAR``. Nothing on cloudy nights."""
        if not self._active or self.forecast is None:
            return
        now = now or _now()
        async with self._maker() as db:
            rows = (
                await db.execute(
                    select(Device, User)
                    .join(User, User.id == Device.owner_id)
                    .where(User.notify_tonight.is_(True))
                )
            ).all()
            for device, user in rows:
                result = await tonight.for_device(self.forecast, device.last_status, now)
                if result is None or result.night.sunset is None:
                    continue
                night, sunset = result.night, result.night.sunset
                night_id = sunset.astimezone(result.zone).strftime("%Y%m%d")
                if device.tonight_night == night_id or now < sunset - TONIGHT_BEFORE_SUNSET:
                    continue
                device.tonight_night = night_id
                await db.commit()
                if night.dusk is None or now > night.dusk or result.best is None:
                    continue
                if result.best[1] - result.best[0] < TONIGHT_MIN_CLEAR:
                    continue
                moon = (
                    f"Mond: {result.moon_phase}, {night.moon_illumination * 100:.0f} % beleuchtet."
                )
                await self._deliver(
                    db,
                    user,
                    Message(
                        title=f"Heute Nacht klar über {device.name}",
                        body=f"{result.summary} {moon}",
                        data={"camera": device.id},
                    ),
                )

    async def check_offline_mail(
        self, registry: ConnectionRegistry, now: dt.datetime | None = None
    ) -> None:
        """Roadmap C14/#15: an e-mail once per outage when a camera is silent for hours,
        for owners who want to hear about problems. Push may not reach a phone for that long."""
        if self.mailer is None or not self.mailer.enabled:
            return
        now = now or _now()
        async with self._maker() as db:
            rows = (
                await db.execute(
                    select(Device, User)
                    .join(User, User.id == Device.owner_id)
                    .where(
                        User.notify_offline.is_(True),
                        Device.last_seen_at < now - OFFLINE_MAIL_AFTER,
                        Device.offline_mailed_at.is_(None),
                    )
                )
            ).all()
            for device, user in rows:
                if registry.is_online(device.id):
                    continue
                device.offline_mailed_at = now
                await db.commit()
                hours = int((now - (device.last_seen_at or now)).total_seconds() // 3600)
                await self.mailer.send(
                    Mail(
                        to=user.email,
                        subject=f"allskyhub: {device.name} ist seit {hours} Stunden offline",
                        body=(
                            f"Hallo,\n\ndeine Kamera „{device.name}“ hat sich seit {hours} "
                            "Stunden nicht beim Hub gemeldet. Sie nimmt weiter auf, wenn nur das "
                            "Netz fehlt; Strom, WLAN oder Router sind die üblichen Ursachen.\n\n"
                            "Sobald sie wieder verbunden ist, holt der Hub nach, was fehlt.\n\n"
                            "allskyhub\n"
                        ),
                    )
                )

    async def run_offline_checks(self, registry: ConnectionRegistry) -> None:
        while True:
            try:
                await self.check_offline_mail(registry)
            except Exception:
                log.exception("mail: offline check failed")
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
            try:
                await self.check_tonight()
            except Exception:
                log.exception("push: evening notice failed")
            await asyncio.sleep(OFFLINE_CHECK.total_seconds())


def _zone() -> dt.tzinfo:
    from allskyhub_server.web.templating import UI_ZONE

    return UI_ZONE
