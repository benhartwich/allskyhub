"""The app's sky view (roadmap N6): what lies in which direction above the camera now.

Directions only (altitude, azimuth in degrees); the app turns them into the phone's view
from its own orientation. Constellations from ``allskyhub_sky``, moon and planets, and the
meteors of the last hours with both track ends (SPEC §6.4), so the phone can point to where
a meteor flew. The camera's place stands for the phone's: they are usually near.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from allskyhub_server.api.app import AppUser
from allskyhub_server.devices import events, queries
from allskyhub_server.models import EventRecord
from allskyhub_server.web.deps import DbSession
from allskyhub_sky import alt_az, precess, sidereal_deg, sky_lines
from allskyhub_sky.astro import moon_radec
from allskyhub_sky.objects import PLANETS, planet_radec

router = APIRouter(prefix="/api/v1")

METEOR_HOURS = 12
METEOR_MAX = 50
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


class SkyView(BaseModel):
    at: dt.datetime
    lines: list[list[tuple[float, float]]]
    objects: list[SkyObjectItem]
    meteors: list[MeteorItem]


@router.get("/cameras/{device_id}/sky-view")
async def sky_view(db: DbSession, user: AppUser, device_id: str) -> SkyView:
    found = await queries.visible_one(db, user.id, device_id)
    if found is None:
        raise HTTPException(404)
    device = found[0]
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        raise HTTPException(409, "Die Kamera kennt ihren Standort noch nicht.")
    now = dt.datetime.now(dt.UTC)
    sky = sky_lines(float(lat), float(lon), now)
    lst = sidereal_deg(now, float(lon))
    objects = [SkyObjectItem(label=name, kind="constellation", alt=alt, az=az)
               for name, alt, az in sky.names]  # fmt: skip
    for key, label in PLANETS.items():
        alt, az = alt_az(*precess(*planet_radec(key, now), now), lst, float(lat))
        if alt >= MIN_ALT:
            objects.append(SkyObjectItem(label=label, kind="planet", alt=alt, az=az))
    alt, az = alt_az(*moon_radec(now), lst, float(lat))
    if alt >= MIN_ALT:
        objects.append(SkyObjectItem(label="Mond", kind="moon", alt=alt, az=az))
    rows = await db.scalars(
        select(EventRecord)
        .where(
            EventRecord.device_id == device.id,
            EventRecord.kind == "meteor",
            EventRecord.start >= now - dt.timedelta(hours=METEOR_HOURS),
            events.counted(),
        )
        .order_by(EventRecord.start.desc())
        .limit(METEOR_MAX)
    )
    meteors: list[MeteorItem] = []
    for event in rows:
        data: dict[str, Any] = event.data or {}
        ends = [data.get(k) for k in ("start_altitude_deg", "start_azimuth_deg",
                                      "end_altitude_deg", "end_azimuth_deg")]  # fmt: skip
        if all(isinstance(v, int | float) for v in ends):
            a1, z1, a2, z2 = (float(v) for v in ends)  # pyright: ignore[reportArgumentType]
            meteors.append(MeteorItem(at=event.start, start=(a1, z1), end=(a2, z2)))
    return SkyView(at=now, lines=sky.lines, objects=objects, meteors=meteors)
