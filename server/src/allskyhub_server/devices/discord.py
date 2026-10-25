"""Detections and the night's summary in a Discord channel (roadmap H5).

The owner creates a webhook in their Discord server and enters its address on the hub,
never on the camera. Detections go out with their picture as soon as the camera sent it
(or right away when there is none), each thunderstorm once; in the morning the night in
one line with the star trails or keogram. Links point to the public page when it is on;
the private camera pages would mean nothing to the channel's members.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import EventVariant, FrameVariant
from allskyhub_server.devices import astro, events, night_summary, tonight
from allskyhub_server.devices.event_text import EVENT_TITLES
from allskyhub_server.devices.events import storm_key
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, EventRecord

log = logging.getLogger(__name__)

# Only Discord's own webhook addresses: the hub never posts to an address of the owner's
# choosing (no requests into other networks).
WEBHOOK = re.compile(
    r"^https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/webhooks/\d{5,30}/[\w-]{20,100}$"
)
KINDS = {
    "meteor": "Meteore",
    "aurora": "Polarlicht",
    "nlc": "Leuchtende Nachtwolken",
    "lightning": "Gewitter (einmal pro Gewitter)",
}
Kind = Literal["meteor", "aurora", "nlc", "lightning"]
# Detections older than this are history, not news (an import, a camera that was offline).
TOO_OLD = dt.timedelta(hours=6)
# At most one detection post per camera in this time (a meteor shower is many events).
QUIET = dt.timedelta(minutes=2)
AFTER_SUNRISE = dt.timedelta(minutes=30)
TIMEOUT_S = 15.0
CHECK_EVERY_S = 300
SUMMARY_PICTURES = ("startrails.jpg", "keogram.jpg")


class DiscordSettings(BaseModel):
    """``device.discord``; ``broken`` is set when Discord says the webhook is gone."""

    webhook: str = Field(max_length=300)
    kinds: list[Kind] = Field(default_factory=lambda: ["meteor", "aurora", "nlc"])
    summary: bool = True
    broken: bool = False

    @field_validator("webhook")
    @classmethod
    def _discord_only(cls, value: str) -> str:
        value = value.strip()
        if not WEBHOOK.fullmatch(value):
            raise ValueError("not a Discord webhook")
        return value


BAD_WEBHOOK = (
    "Das ist keine Discord-Webhook-Adresse. Sie beginnt mit "
    "https://discord.com/api/webhooks/ (in Discord: Servereinstellungen → Integrationen → "
    "Webhooks → Webhook-URL kopieren)."
)
BAD_WEBHOOK_EN = (
    "This is not a Discord webhook address. It starts with "
    "https://discord.com/api/webhooks/ (in Discord: Server Settings → Integrations → "
    "Webhooks → Copy Webhook URL)."
)


def settings_of(device: Device) -> DiscordSettings | None:
    try:
        return DiscordSettings.model_validate(device.discord) if device.discord else None
    except ValidationError:
        return None


def masked(webhook: str) -> str:
    """The address without its secret part, for the page."""
    head, _, _ = webhook.rpartition("/")
    return f"{head}/…"


class Poster:
    """Posts to webhooks; one shared HTTP client."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def post(
        self, webhook: str, text: str, picture: bytes | None = None, name: str = "bild.jpg"
    ) -> Literal["ok", "gone", "failed"]:
        no_mentions: list[str] = []
        payload: dict[str, Any] = {
            "content": text[:2000],
            "allowed_mentions": {"parse": no_mentions},
        }
        files = None
        if picture is not None:
            files = {
                "payload_json": (None, json.dumps(payload), "application/json"),
                "files[0]": (name, picture, "image/jpeg"),
            }
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
        try:
            if files is not None:
                r = await client.post(webhook, files=files)
            else:
                r = await client.post(webhook, json=payload)
        except httpx.HTTPError:
            log.warning("discord: post failed")
            return "failed"
        finally:
            if self._client is None:
                await client.aclose()
        if r.status_code in (401, 403, 404):
            return "gone"
        return "ok" if r.is_success else "failed"


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


class Discord:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        images: ImageStore,
        poster: Poster,
        base_url: str,
    ) -> None:
        self._maker = maker
        self._images = images
        self._poster = poster
        self._base = base_url.rstrip("/")
        self._last: dict[str, dt.datetime] = {}

    def _public(self, device: Device, path: str = "") -> str:
        if not device.public_slug:
            return ""
        return f"\n{self._base}/sky/{device.public_slug}{path}"

    async def _send(
        self, db: AsyncSession, device: Device, text: str, picture: bytes | None
    ) -> bool:
        config = settings_of(device)
        if config is None or config.broken:
            return False
        result = await self._poster.post(config.webhook, text, picture)
        if result == "gone":
            # Deleted in Discord: stop trying and tell the owner on the camera page.
            device.discord = {**config.model_dump(), "broken": True}
            await db.commit()
        return result == "ok"

    async def event(self, device_id: str, event_id: str, *, picture_arrived: bool) -> None:
        """A new detection (``picture_arrived`` False) or its picture (True). Detections
        with a picture wait for it; the rest go out at once. Each only once."""
        now = dt.datetime.now(dt.UTC)
        try:
            async with self._maker() as db:
                row = (
                    await db.execute(
                        select(Device, EventRecord)
                        .join(EventRecord, EventRecord.device_id == Device.id)
                        .where(Device.id == device_id, EventRecord.event_id == event_id)
                    )
                ).one_or_none()
                if row is None:
                    return
                device, event = row
                config = settings_of(device)
                if config is None or config.broken or event.kind not in config.kinds:
                    return
                if event.discord_posted or events.dismissed(event):
                    return
                if event.has_image and not picture_arrived:
                    return  # wait for the picture
                if now - event.start > TOO_OLD or (event.data or {}).get("imported"):
                    return
                storm = storm_key(event)
                if storm is not None:
                    posted = await db.scalar(
                        select(EventRecord.id).where(
                            EventRecord.device_id == device_id,
                            EventRecord.kind == "lightning",
                            EventRecord.discord_posted.is_(True),
                            EventRecord.data["storm"].astext == storm,
                        )
                    )
                    if posted is not None:
                        return
                else:
                    last = self._last.get(device_id)
                    if last is not None and now - last < QUIET:
                        return
                    self._last[device_id] = now
                event.discord_posted = True
                await db.commit()
                picture = None
                if event.has_image:
                    picture = _read(
                        self._images.event_path(
                            device_id, event.night_id, event.event_id, EventVariant.THUMB
                        )
                    )
                title = "Gewitter" if storm else EVENT_TITLES.get(event.kind, event.kind)
                zone = tonight.zone_for(_settings(device).get("timezone"))
                at = event.start.astimezone(zone).strftime("%H:%M")
                text = f"**{title} über {device.name}** um {at} Uhr" + self._public(device)
                await self._send(db, device, text, picture)
        except Exception:
            log.exception("discord: event post failed")

    async def check_morning(self, now: dt.datetime | None = None) -> None:
        """One pass: each camera's night in one line, once, after sunrise at the camera."""
        now = now or dt.datetime.now(dt.UTC)
        async with self._maker() as db:
            devices = list(
                await db.scalars(
                    select(Device).where(Device.discord.is_not(None), Device.owner_id.is_not(None))
                )
            )
            for device in devices:
                config = settings_of(device)
                if config is None or config.broken or not config.summary:
                    continue
                settings = _settings(device)
                lat, lon = settings.get("latitude"), settings.get("longitude")
                if not isinstance(lat, int | float) or not isinstance(lon, int | float):
                    continue
                zone = tonight.zone_for(settings.get("timezone"))
                night_id = night_summary.last_night_id(now, zone)
                if device.discord_night == night_id:
                    continue
                evening = dt.datetime.strptime(night_id, "%Y%m%d").replace(hour=12, tzinfo=zone)
                night = astro.night_after(evening.astimezone(dt.UTC), float(lat), float(lon))
                if night.sunrise is None or now < night.sunrise + AFTER_SUNRISE:
                    continue
                await db.execute(
                    update(Device).where(Device.id == device.id).values(discord_night=night_id)
                )
                await db.commit()
                if now.astimezone(zone).hour >= 12:
                    continue  # too late to be news
                summary = await night_summary.summarize(db, device.id, night_id)
                if summary.empty:
                    continue
                picture = None
                for name in SUMMARY_PICTURES:
                    picture = _read(
                        self._images.product_path(device.id, night_id, name, FrameVariant.THUMB)
                    ) or _read(
                        self._images.product_path(device.id, night_id, name, FrameVariant.FULL)
                    )
                    if picture is not None:
                        break
                text = f"**Die Nacht über {device.name}**\n{summary.line()}" + self._public(
                    device, f"/nights/{night_id}"
                )
                await self._send(db, device, text, picture)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.check_morning()
            except Exception:
                log.exception("discord: morning summary failed")
            await asyncio.sleep(CHECK_EVERY_S)

    async def test(self, device: Device) -> Literal["ok", "gone", "failed"]:
        config = settings_of(device)
        if config is None:
            return "failed"
        return await self._poster.post(
            config.webhook,
            f"allskyhub ist verbunden: Hier erscheinen die Erkennungen von {device.name}.",
        )


def _settings(device: Device) -> dict[str, Any]:
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    return settings
