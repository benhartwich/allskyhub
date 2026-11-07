"""SharedSky in numbers for the app (devices/network.py): the network as on /netz, without
login, and for the owner of a station or camera its part and "Dein Himmel im Vergleich".

Names come as ``public.credit`` gives them to anyone; a link to a public page becomes its
slug, which the app opens like a camera in "Entdecken".
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from allskyhub_server.api.app import AppUser
from allskyhub_server.devices import network
from allskyhub_server.i18n import tr
from allskyhub_server.models import Device
from allskyhub_server.web.deps import DbSession

router = APIRouter(prefix="/api/v1")

CACHE = "public, max-age=60"


def _slug(link: str | None) -> str | None:
    return link.removeprefix("/sky/") if link and link.startswith("/sky/") else None


class FireballItem(BaseModel):
    id: str
    at: dt.datetime
    high_km: float
    low_km: float
    cameras: list[str]  # the cameras' names, as results name them


class Shower(BaseModel):
    name: str
    count: int


class RankedItem(BaseModel):
    name: str
    slug: str | None
    kind: str
    sqm: float


class MemberItem(BaseModel):
    name: str
    slug: str | None
    kind: str
    software: str | None
    since: dt.datetime | None
    active: bool
    meteors: int
    fireballs: int


class PointItem(BaseModel):
    lat: float
    lon: float
    title: str
    slug: str | None
    kind: str
    active: bool


class NetworkOut(BaseModel):
    cameras: int
    stations: int
    active: int
    meteors_tonight: int
    fireballs_month: int
    showers_tonight: list[Shower]
    tonight: list[FireballItem]
    darkest: list[RankedItem]
    members: list[MemberItem]
    points: list[PointItem]


class NightItem(BaseModel):
    night_id: str
    own: float | None
    low: float | None
    high: float | None


class ComparisonOut(BaseModel):
    nights: list[NightItem]
    neighbours: int
    difference: float | None


class PartOut(BaseModel):
    since: dt.datetime | None
    fireballs: int
    meteors: int
    recent: list[FireballItem]
    comparison: ComparisonOut | None


def _fireball(f: network.Fireball) -> FireballItem:
    return FireballItem(id=str(f.pair_id), at=f.at, high_km=f.high_km, low_km=f.low_km,
                        cameras=[str(o["name"]) for o in f.others])  # fmt: skip


def _shower(name: str) -> str:
    return tr("sporadisch", "sporadic") if name == "sporadisch" else name


@router.get("/public/network")
async def network_view(request: Request, db: DbSession) -> JSONResponse:
    cache: network.OverviewCache = request.app.state.network
    view = await cache.get(db, dt.datetime.now(dt.UTC))
    showers = sorted(view.showers_tonight.items(), key=lambda kv: -kv[1])
    out = NetworkOut(
        cameras=view.cameras,
        stations=view.stations,
        active=view.active,
        meteors_tonight=view.meteors_tonight,
        fireballs_month=view.fireballs_month,
        showers_tonight=[Shower(name=_shower(n), count=c) for n, c in showers],
        tonight=[_fireball(f) for f in view.tonight],
        darkest=[RankedItem(name=r.name, slug=_slug(r.link), kind=r.kind, sqm=r.sqm)
                 for r in view.darkest[:10]],
        members=[MemberItem(name=m.name, slug=_slug(m.link), kind=m.kind, software=m.software,
                            since=m.since, active=m.active, meteors=m.meteors,
                            fireballs=m.fireballs) for m in view.members],
        points=[PointItem(lat=p.lat, lon=p.lon, title=p.title, slug=p.slug, kind=p.kind,
                          active=p.active) for p in view.points],
    )  # fmt: skip
    return JSONResponse(out.model_dump(mode="json"), headers={"Cache-Control": CACHE})


@router.get("/devices/{device_id}/network")
async def device_part(db: DbSession, user: AppUser, device_id: str) -> PartOut:
    """The owner's own camera or station: "Im Netz" and "Dein Himmel im Vergleich"."""
    device = await db.get(Device, device_id)
    if device is None or device.owner_id != user.id:
        raise HTTPException(404, "Not found")
    part = await network.part(db, device)
    found = await network.comparison(db, device, dt.datetime.now(dt.UTC))
    comparison = None
    if found is not None:
        comparison = ComparisonOut(
            nights=[NightItem(night_id=n.night_id, own=n.own, low=n.low, high=n.high)
                    for n in found.nights],
            neighbours=found.neighbours,
            difference=found.difference,
        )  # fmt: skip
    return PartOut(
        since=part.since,
        fireballs=part.fireballs,
        meteors=part.meteors,
        recent=[_fireball(f) for f in part.recent],
        comparison=comparison,
    )
