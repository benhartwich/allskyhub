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
from allskyhub_sky import alt_az, precess, sidereal_deg, sky_lines
from allskyhub_sky.astro import moon_radec
from allskyhub_sky.objects import PLANETS, planet_radec

router = APIRouter(prefix="/api/v1")

METEOR_HOURS = 12
METEOR_MAX = 50
OVERFLIGHT_MAX = 150
# Planets this far below the horizon are of no use to point at.
MIN_ALT = -5.0


class SkyObjectItem(BaseModel):
    label: str
    kind: str  # "moon", "planet", "constellation"
    alt: float
    az: float


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
    sky = sky_lines(float(lat), float(lon), now, latin=english())
    lst = sidereal_deg(now, float(lon))
    objects = [SkyObjectItem(label=name, kind="constellation", alt=alt, az=az)
               for name, alt, az in sky.names]  # fmt: skip
    for key, label in PLANETS.items():
        label = key.capitalize() if english() else label  # the keys are the English names
        alt, az = alt_az(*precess(*planet_radec(key, now), now), lst, float(lat))
        if alt >= MIN_ALT:
            objects.append(SkyObjectItem(label=label, kind="planet", alt=alt, az=az))
    alt, az = alt_az(*moon_radec(now), lst, float(lat))
    if alt >= MIN_ALT:
        objects.append(SkyObjectItem(label=tr("Mond", "Moon"), kind="moon", alt=alt, az=az))
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
    return SkyView(
        at=now, lines=sky.lines, objects=objects, meteors=meteors, overflights=overflights
    )
