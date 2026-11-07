"""Creating, showing and removing a station (SPEC §10.1, §10.7), the same for the web pages
(web/routes_stations.py) and the app (api/app_stations.py): the same checks, limits and
mails to the admins, only the answer differs (a page or JSON).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import image_url, pairing, public, stations
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.i18n import tr
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.models import Device, EventRecord, Frame, StationSource, User
from allskyhub_server.settings import Settings

log = logging.getLogger(__name__)

SOFTWARE = ("indi-allsky", "image-url", "other")


class StationInputError(Exception):
    """What the owner has to fix, in the language of the request (i18n.tr)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Ask:
    """The answers of "Kamera verbinden"."""

    software: str
    name: str
    terms: bool
    url: str = ""
    latitude: str = ""
    longitude: str = ""
    elevation_m: str = ""
    timezone: str = ""
    focal_mm: str = ""
    proof_text: str = ""
    proof: bytes = b""
    visibility: public.Visibility = field(default_factory=public.Visibility)


@dataclass(frozen=True)
class Place:
    lat: float
    lon: float
    elevation_m: float | None


def place(lat: str, lon: str, elevation: str) -> Place:
    try:
        la, lo = float(lat.replace(",", ".")), float(lon.replace(",", "."))
        el = float(elevation.replace(",", ".")) if elevation.strip() else None
    except ValueError:
        raise StationInputError(
            tr(
                "Bitte den Ort auf der Karte setzen oder die Koordinaten eingeben.",
                "Please set the location on the map or enter the coordinates.",
            )
        ) from None
    if not (-90 <= la <= 90 and -180 <= lo <= 180) or (el is not None and not -500 <= el <= 9000):
        raise StationInputError(
            tr("Die Koordinaten liegen außerhalb der Erde.", "The coordinates are off the Earth.")
        )
    return Place(la, lo, el)


def number(text: str) -> bool:
    try:
        return float(text.replace(",", ".")) > 0
    except ValueError:
        return False


def proofs_dir(settings: Settings) -> Path:
    return settings.data_dir / "station-proofs"


def masks_dir(settings: Settings) -> Path:
    return settings.data_dir / "station-masks"


async def create(
    db: AsyncSession,
    user: User,
    ask: Ask,
    key: SecretKey,
    settings: Settings,
    mailer: Mailer,
) -> tuple[Device, str | None]:
    """SPEC §10.1 creating a station, §10.7 asking with an image address. The secret
    once (None for an image address, which has none). Commits. Raises StationInputError."""
    if ask.software not in SOFTWARE:
        raise StationInputError(
            tr("Bitte die Software deiner Kamera wählen.", "Please choose your camera's software.")
        )
    if not ask.name.strip():
        raise StationInputError(
            tr("Bitte einen Namen für die Station angeben.", "Please give the station a name.")
        )
    if not ask.terms:
        raise StationInputError(
            tr("Bitte den Teilnahmebedingungen zustimmen.", "Please agree to the terms.")
        )
    if ask.software != "image-url":
        device, secret = await stations.create(db, user, ask.name, ask.software, key)
        public.set_visibility(device, ask.visibility)  # SPEC §10.6 Visibility
        await db.commit()
        log.info("station created", extra={"device_id": device.id, "software": ask.software})
        return device, secret
    try:
        image_url.check_url(ask.url)
    except image_url.FetchError as exc:
        raise StationInputError(exc.text) from None
    where = place(ask.latitude, ask.longitude, ask.elevation_m)
    if len(ask.proof) > image_url.PROOF_MAX_UPLOAD:
        raise StationInputError(
            tr("Das Nachweis-Bild ist zu groß.", "The proof image is too large.")
        )
    if not ask.proof_text.strip() and not ask.proof:
        raise StationInputError(
            tr(
                "Bitte einen Nachweis angeben: einen Satz, einen Link oder ein Bild.",
                "Please give a proof: a sentence, a link or an image.",
            )
        )
    device, _ = await stations.create(db, user, ask.name, ask.software, key)
    public.set_visibility(device, ask.visibility)  # shown once an admin approved it (§10.7)
    if ask.proof:
        try:
            image_url.store_proof(proofs_dir(settings), device.id, ask.proof)
        except image_url.FetchError as exc:
            await db.rollback()
            raise StationInputError(
                tr("Das Nachweis-Bild: ", "The proof image: ") + exc.text
            ) from None
    focal = ask.focal_mm.replace(",", ".")
    lens = {"focal_mm": float(focal)} if number(ask.focal_mm) else None
    device.last_status = {
        "settings": {"latitude": where.lat, "longitude": where.lon,
                     "elevation_m": where.elevation_m, "timezone": ask.timezone.strip() or None},
        "station": {"software": "image-url", "lens": lens},
    }  # fmt: skip
    db.add(StationSource(
        device_id=device.id, url=ask.url.strip(), proof_text=ask.proof_text.strip(),
        proof_image=bool(ask.proof),
    ))  # fmt: skip
    await db.commit()
    log.info("station asked by image address", extra={"device_id": device.id})
    await tell_admins(settings, mailer, device, user)
    return device, None


async def tell_admins(settings: Settings, mailer: Mailer, device: Device, user: User) -> None:
    if not mailer.enabled:
        return
    link = f"{settings.base_url.rstrip('/')}/admin/stationen/{device.id}"
    for admin in settings.admins:
        await mailer.send(Mail(
            to=admin, subject=f"SharedSky: {device.name} wartet auf Prüfung",
            body=f"Hallo,\n\n{user.email} möchte eine Kamera über ihre Bild-Adresse "
                 f"verbinden:\n\n{link}\n\nallskyhub\n",
        ))  # fmt: skip


async def today(db: AsyncSession, device: Device) -> tuple[int, int]:
    """Frames and meteors of the station's current night."""
    night = stations.night_id(dt.datetime.now(dt.UTC), stations.longitude(device))
    frames = await db.scalar(
        select(func.count())
        .select_from(Frame)
        .where(Frame.device_id == device.id, Frame.night_id == night)
    )
    meteors = await db.scalar(
        select(func.count())
        .select_from(EventRecord)
        .where(
            EventRecord.device_id == device.id,
            EventRecord.night_id == night,
            EventRecord.kind == "meteor",
        )
    )
    return int(frames or 0), int(meteors or 0)


async def remove(db: AsyncSession, device: Device, state: Any, settings: Settings) -> None:
    """SPEC §10.1: images, the exact place and the secret go at once; measurements stay.
    ``state`` is the app's state (images, the station detector). Commits."""
    source = await db.get(StationSource, device.id)
    if source is not None:
        await db.delete(source)
    await pairing.unpair(db, device)
    await db.commit()
    store: ImageStore = state.images
    store.delete_device(device.id)
    image_url.proof_path(proofs_dir(settings), device.id).unlink(missing_ok=True)
    image_url.OverlayMask(masks_dir(settings)).forget(device.id)
    state.station_detector.buffer.drop(device.id)
    log.info("station removed", extra={"device_id": device.id})
