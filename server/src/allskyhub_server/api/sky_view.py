"""The app's sky view (roadmap N6): what lies in which direction above the camera now.

Directions only (altitude, azimuth in degrees); the app turns them into the phone's view
from its own orientation. Constellations from ``allskyhub_sky``, moon and planets, and the
meteors and overflights (satellites, aircraft) of the last hours with both track ends
(SPEC §6.4), so the phone can point to where they flew. The camera's place stands for the
phone's: they are usually near.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from allskyhub_server.api.app import AppUser
from allskyhub_server.devices import events, queries
from allskyhub_server.i18n import english, tr
from allskyhub_server.models import EventRecord
from allskyhub_server.web.deps import DbSession
from allskyhub_sky import alt_az, constellations, precess, sidereal_deg, sky_lines
from allskyhub_sky.astro import moon_radec
from allskyhub_sky.objects import MESSIER_EN, PLANETS, planet_radec
from allskyhub_sky.objects import _messier as messier  # pyright: ignore[reportPrivateUsage]

router = APIRouter(prefix="/api/v1")

METEOR_HOURS = 12
METEOR_MAX = 50
OVERFLIGHT_MAX = 150
# Planets this far below the horizon are of no use to point at.
MIN_ALT = -5.0


class SkyObjectItem(BaseModel):
    label: str
    kind: str  # "moon", "planet", "constellation", or a Messier kind (allskyhub_sky.describe)
    alt: float
    az: float
    # For the app's search and object info (public sky view); older apps ignore them.
    key: str | None = None
    mag: float | None = None


class MeteorItem(BaseModel):
    at: dt.datetime
    start: tuple[float, float]  # altitude, azimuth
    end: tuple[float, float]


class OverflightItem(BaseModel):
    """SPEC §6.4 Overflights: a satellite, an aircraft or an unknown moving object."""

    at: dt.datetime
    start: tuple[float, float]  # altitude, azimuth
    end: tuple[float, float]
    kind: str  # satellite, aircraft, unknown
    name: str | None


class SkyView(BaseModel):
    at: dt.datetime
    lines: list[list[tuple[float, float]]]
    objects: list[SkyObjectItem]
    meteors: list[MeteorItem]
    # Newer than the first app version, which ignores it.
    overflights: list[OverflightItem] = []


@router.get("/cameras/{device_id}/sky-view")
async def sky_view(db: DbSession, user: AppUser, device_id: str) -> SkyView:
    found = await queries.visible_one(db, user.id, device_id)
    if found is None:
        raise HTTPException(404)
    device = found[0]
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        raise HTTPException(
            409,
            tr(
                "Die Kamera kennt ihren Standort noch nicht.",
                "The camera does not know its location yet.",
            ),
        )
    now = dt.datetime.now(dt.UTC)
    lines, objects = sky_objects(float(lat), float(lon), now)
    rows = await db.scalars(
        select(EventRecord)
        .where(
            EventRecord.device_id == device.id,
            EventRecord.kind == "meteor",
            EventRecord.start >= now - dt.timedelta(hours=METEOR_HOURS),
        )
        .order_by(EventRecord.start.desc())
    )
    meteors: list[MeteorItem] = []
    overflights: list[OverflightItem] = []
    for event in rows:
        data: dict[str, Any] = event.data or {}
        ends = [data.get(k) for k in ("start_altitude_deg", "start_azimuth_deg",
                                      "end_altitude_deg", "end_azimuth_deg")]  # fmt: skip
        if not all(isinstance(v, int | float) for v in ends):
            continue
        a1, z1, a2, z2 = (float(v) for v in ends)  # pyright: ignore[reportArgumentType]
        if (kind := events.overflight(event)) is not None:
            if len(overflights) < OVERFLIGHT_MAX:
                overflights.append(OverflightItem(
                    at=event.start, start=(a1, z1), end=(a2, z2), kind=kind,
                    name=events.overflight_name(event),
                ))  # fmt: skip
        elif not events.dismissed(event) and len(meteors) < METEOR_MAX:
            meteors.append(MeteorItem(at=event.start, start=(a1, z1), end=(a2, z2)))
    return SkyView(at=now, lines=lines, objects=objects, meteors=meteors, overflights=overflights)


def sky_objects(
    lat: float, lon: float, now: dt.datetime, *, catalogue: bool = False
) -> tuple[list[list[tuple[float, float]]], list[SkyObjectItem]]:
    """Constellation lines and the objects to label. With ``catalogue`` (the public sky
    view and its search): every constellation, planet and Messier object, also below the
    horizon, each with its key and brightness."""
    sky = sky_lines(lat, lon, now, latin=english())
    lst = sidereal_deg(now, lon)
    floor = -90.0 if catalogue else MIN_ALT
    objects: list[SkyObjectItem] = []
    if catalogue:
        for key, (name, ra, dec) in constellations(latin=english()).items():
            alt, az = alt_az(*precess(ra, dec, now), lst, lat)
            objects.append(SkyObjectItem(label=name, kind="constellation", alt=alt, az=az, key=key))
    else:
        objects += [SkyObjectItem(label=name, kind="constellation", alt=alt, az=az)
                    for name, alt, az in sky.names]  # fmt: skip
    for key, label in PLANETS.items():
        label = key.capitalize() if english() else label  # the keys are the English names
        alt, az = alt_az(*precess(*planet_radec(key, now), now), lst, lat)
        if alt >= floor:
            objects.append(
                SkyObjectItem(label=label, kind="planet", alt=alt, az=az,
                              key=key if catalogue else None)
            )  # fmt: skip
    alt, az = alt_az(*moon_radec(now), lst, lat)
    if alt >= floor:
        objects.append(SkyObjectItem(label=tr("Mond", "Moon"), kind="moon", alt=alt, az=az,
                                     key="moon" if catalogue else None))  # fmt: skip
    if catalogue:
        for key, (name, ra, dec, mag, kind) in messier().items():
            if english() and name:
                name = MESSIER_EN.get(key, name)
            alt, az = alt_az(*precess(float(ra), float(dec), now), lst, lat)
            objects.append(SkyObjectItem(
                label=f"{key} {name}" if name else key, kind=str(kind), alt=alt, az=az,
                key=key, mag=float(mag) if mag is not None else None,
            ))  # fmt: skip
    return sky.lines, objects
