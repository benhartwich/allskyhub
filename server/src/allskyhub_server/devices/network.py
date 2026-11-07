"""The network in numbers (SharedSky, SPEC §10.6; Benjamin's draft of 2026-11-07): what a
camera or station adds to it ("Im Netz"), its sky next to its neighbours' ("Himmel im
Vergleich") and the network's page /netz.

Only what is public anyway: fireballs both of whose cameras joined the network (as on
/feuerkugeln), cameras and stations by ``public.credit``, places only as the owners show
them (0.1°). Neighbours' sky values appear only as a range over at least
``MIN_NEIGHBOURS`` of them, never one by one.
"""

from __future__ import annotations

import datetime as dt
import statistics
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import fireball, public, stations
from allskyhub_server.models import Device, MeteorPair, NightStat, StationSource

NEIGHBOUR_KM = 150.0
COMPARE_NIGHTS = 30
MIN_NIGHTS = 5  # of the camera itself and of each neighbour in the window
MIN_NEIGHBOURS = 2
SQM_CLEAR_HOURS = 1.0
# Three cameras give three pairs of one meteor: pairs this close in time are one.
SAME_METEOR_S = 30
RECENT_FIREBALLS = 3
ACTIVE_WITHIN = dt.timedelta(hours=12)
# Where the network's night is counted: Central Europe, where nearly all cameras stand.
NETWORK_LONGITUDE = 10.0


@dataclass(frozen=True)
class Fireball:
    pair_id: uuid.UUID
    at: dt.datetime
    high_km: float
    low_km: float
    others: list[dict[str, str | None]]  # the other cameras, as public.credit names them


@dataclass(frozen=True)
class Part:
    """What a camera or station adds to the network ("Im Netz")."""

    since: dt.datetime | None
    fireballs: int
    meteors: int
    recent: list[Fireball]


@dataclass(frozen=True)
class NightValue:
    night_id: str
    own: float | None
    low: float | None  # the neighbours' range that night
    high: float | None


@dataclass(frozen=True)
class Comparison:
    """The darkest reading of each clear night (``NightStat.best_sqm``), mag/arcsec²."""

    nights: list[NightValue]
    neighbours: int
    # The camera minus the neighbours' median, averaged over nights both have; positive:
    # darker than the neighbours.
    difference: float | None

    def chart(self, width: int = 520, height: int = 230) -> dict[str, Any]:
        """Marks for an SVG drawn to one scale: the neighbours' range as bars, the camera
        as a line broken at nights without a value, ticks every half magnitude."""
        left, right, top, bottom = 46, 12, 12, 30
        values = [v for n in self.nights for v in (n.own, n.low, n.high) if v is not None]
        lo = (int(min(values) * 2) / 2) if values else 19.0
        hi = (int(max(values) * 2) / 2 + 0.5) if values else 22.0
        span = max(hi - lo, 0.5)
        count = max(len(self.nights) - 1, 1)

        def x(i: int) -> float:
            return round(left + i * (width - left - right) / count, 1)

        def y(v: float) -> float:
            return round(top + (hi - v) / span * (height - top - bottom), 1)

        bars = [
            {"x": x(i) - 4, "y": y(n.high), "h": max(2.0, round(y(n.low) - y(n.high), 1))}
            for i, n in enumerate(self.nights)
            if n.low is not None and n.high is not None
        ]
        path, pen = "", False
        dots: list[dict[str, float]] = []
        for i, n in enumerate(self.nights):
            if n.own is None:
                pen = False
                continue
            path += f"{'L' if pen else 'M'}{x(i)} {y(n.own)} "
            pen = True
            dots.append({"x": x(i), "y": y(n.own)})
        steps = round(span * 2)
        ticks = [{"y": y(lo + k / 2), "label": f"{lo + k / 2:.1f}"} for k in range(steps + 1)]
        days = [0, len(self.nights) // 2, len(self.nights) - 1] if self.nights else []
        return {
            "width": width,
            "height": height,
            "left": left,
            "right": width - right,
            "bars": bars,
            "path": path.strip(),
            "last": dots[-1] if dots else None,
            "ticks": ticks,
            "days": [{"x": x(i), "night_id": self.nights[i].night_id} for i in days],
        }


def place(device: Device) -> tuple[float, float] | None:
    """The exact place, for distances only; never shown."""
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if isinstance(lat, int | float) and isinstance(lon, int | float):
        return float(lat), float(lon)
    return None


def _cameras(pair: MeteorPair) -> list[str]:
    return list(dict.fromkeys(pair.cameras or [pair.device_a, pair.device_b]))


def _public_fireball() -> Any:
    return or_(
        MeteorPair.low_km <= fireball.METEORITE_END_KM, MeteorPair.peak >= fireball.FIREBALL_PEAK
    )


async def _network_pairs(
    db: AsyncSession, since: dt.datetime, device_id: str | None = None
) -> list[MeteorPair]:
    """Fireballs since then whose cameras all joined the network, one pair per meteor,
    newest first; only those with ``device_id`` when given."""
    query = select(MeteorPair).where(MeteorPair.at >= since, _public_fireball())
    if device_id is not None:
        query = query.where(
            or_(
                MeteorPair.device_a == device_id,
                MeteorPair.device_b == device_id,
                MeteorPair.cameras.contains([device_id]),
            )
        )
    out: list[MeteorPair] = []
    for pair in await db.scalars(query.order_by(MeteorPair.at.desc()).limit(500)):
        if any(abs((pair.at - p.at).total_seconds()) < SAME_METEOR_S for p in out):
            continue
        devices = [await db.get(Device, d) for d in _cameras(pair)]
        if all(d is not None and d.network for d in devices):
            out.append(pair)
    return out


async def _fireball(db: AsyncSession, pair: MeteorPair, without: str | None) -> Fireball:
    others: list[dict[str, str | None]] = []
    for device_id in _cameras(pair):
        device = await db.get(Device, device_id)
        if device is not None and device_id != without:
            others.append(public.credit(device, None))
    return Fireball(pair.id, pair.at, pair.high_km, pair.low_km, others)


async def part(db: AsyncSession, device: Device) -> Part:
    since = device.terms_accepted_at if device.kind == "station" else device.paired_at
    pairs = await _network_pairs(db, since or dt.datetime(2000, 1, 1, tzinfo=dt.UTC), device.id)
    meteors = await db.scalar(
        select(func.coalesce(func.sum(NightStat.meteors), 0)).where(
            NightStat.device_id == device.id
        )
    )
    recent = [await _fireball(db, p, device.id) for p in pairs[:RECENT_FIREBALLS]]
    return Part(since=since, fireballs=len(pairs), meteors=int(meteors or 0), recent=recent)


async def _nights(
    db: AsyncSession, device_ids: list[str], since: str
) -> dict[str, dict[str, float]]:
    """device → night → darkest reading of a clear night (or of one without cloud values:
    stations do not measure clouds)."""
    rows = await db.execute(
        select(NightStat.device_id, NightStat.night_id, NightStat.best_sqm).where(
            NightStat.device_id.in_(device_ids),
            NightStat.night_id >= since,
            NightStat.best_sqm.is_not(None),
            or_(NightStat.clear_hours.is_(None), NightStat.clear_hours >= SQM_CLEAR_HOURS),
        )
    )
    out: dict[str, dict[str, float]] = {}
    for device_id, night_id, sqm in rows.all():
        if sqm is not None:
            out.setdefault(device_id, {})[night_id] = float(sqm)
    return out


async def neighbours(db: AsyncSession, device: Device) -> list[Device]:
    """Public cameras and stations whose page shows the sky, within ``NEIGHBOUR_KM``."""
    here = place(device)
    if here is None:
        return []
    found: list[Device] = []
    for other in await db.scalars(select(Device).where(public.shown(), Device.id != device.id)):
        there = place(other)
        if there is None or not public.profile(other).sections.sky:
            continue
        if fireball.distance_km(*here, *there) <= NEIGHBOUR_KM:
            found.append(other)
    return found


async def comparison(db: AsyncSession, device: Device, now: dt.datetime) -> Comparison | None:
    """None until the camera and at least ``MIN_NEIGHBOURS`` neighbours have
    ``MIN_NIGHTS`` clear nights each in the last ``COMPARE_NIGHTS``."""
    near = await neighbours(db, device)
    if len(near) < MIN_NEIGHBOURS:
        return None
    last = stations.night_id(now, stations.longitude(device))
    first = dt.date.fromisoformat(last) - dt.timedelta(days=COMPARE_NIGHTS - 1)
    values = await _nights(db, [device.id, *(d.id for d in near)], first.strftime("%Y%m%d"))
    own = values.get(device.id, {})
    others = [v for d, v in values.items() if d != device.id and len(v) >= MIN_NIGHTS]
    if len(own) < MIN_NIGHTS or len(others) < MIN_NEIGHBOURS:
        return None
    nights: list[NightValue] = []
    differences: list[float] = []
    for k in range(COMPARE_NIGHTS):
        night = (first + dt.timedelta(days=k)).strftime("%Y%m%d")
        seen = [v[night] for v in others if night in v]
        mine = own.get(night)
        enough = len(seen) >= MIN_NEIGHBOURS
        nights.append(
            NightValue(
                night_id=night,
                own=mine,
                low=min(seen) if enough else None,
                high=max(seen) if enough else None,
            )
        )
        if mine is not None and enough:
            differences.append(mine - statistics.median(seen))
    difference = round(statistics.mean(differences), 2) if differences else None
    return Comparison(nights=nights, neighbours=len(others), difference=difference)


# /netz


@dataclass(frozen=True)
class Member:
    """One camera or station in the list of who takes part."""

    name: str
    link: str | None
    kind: str  # "camera" or "station"
    software: str | None
    since: dt.datetime | None
    active: bool
    meteors: int
    fireballs: int


@dataclass(frozen=True)
class Point:
    """A dot on the network's map, at the owner's rounding (0.1°)."""

    lat: float
    lon: float
    title: str
    slug: str | None
    kind: str
    active: bool
    image: bool

    def as_json(self) -> dict[str, object]:
        return {
            "lat": self.lat, "lon": self.lon, "title": self.title, "slug": self.slug,
            "kind": self.kind, "active": self.active, "image": self.image, "place": "",
            "sqm": None, "sqm_class": "", "sqm_text": "",
        }  # fmt: skip


@dataclass(frozen=True)
class Ranked:
    name: str
    link: str | None
    kind: str
    sqm: float


@dataclass
class Overview:
    cameras: int = 0
    stations: int = 0
    active: int = 0
    meteors_tonight: int = 0
    showers_tonight: dict[str, int] = field(default_factory=dict[str, int])
    fireballs_month: int = 0
    tonight: list[Fireball] = field(default_factory=list[Fireball])
    darkest: list[Ranked] = field(default_factory=list[Ranked])
    members: list[Member] = field(default_factory=list[Member])
    points: list[Point] = field(default_factory=list[Point])
    night_id: str = ""
    last_night_id: str = ""


async def members(db: AsyncSession) -> list[Device]:
    """Who takes part publicly: cameras with a public page in the network, and stations
    past their week of notice (a station's page and name are its owner's choice; one
    without either is still counted, as "eine Station im Netz"). An image address only
    once an admin checked it (SPEC §10.7)."""
    checked = select(StationSource.device_id).where(StationSource.state == "approved")
    rows = await db.scalars(
        select(Device).where(
            Device.owner_id.is_not(None),
            or_(
                and_(Device.kind == "camera", public.shown()),
                and_(
                    Device.kind == "station",
                    Device.public_from.is_(None),
                    or_(Device.software != "image-url", Device.id.in_(checked)),
                ),
            ),
        )
    )
    return list(rows)


def _active(device: Device, now: dt.datetime) -> bool:
    seen = device.last_seen_at or device.latest_thumb_at
    return seen is not None and now - seen <= ACTIVE_WITHIN


async def overview(db: AsyncSession, now: dt.datetime) -> Overview:
    out = Overview()
    devices = await members(db)
    ids = [d.id for d in devices]
    out.night_id = stations.night_id(now, NETWORK_LONGITUDE)
    out.last_night_id = (dt.date.fromisoformat(out.night_id) - dt.timedelta(days=1)).strftime(
        "%Y%m%d"
    )
    # Before midnight "last night" is yesterday's; after the night is over, the one just gone.
    local_hour = (now + dt.timedelta(hours=NETWORK_LONGITUDE / 15)).hour
    darkest_night = out.night_id if 6 <= local_hour < 12 else out.last_night_id
    stats = {
        (s.device_id, s.night_id): s
        for s in await db.scalars(
            select(NightStat).where(
                NightStat.device_id.in_(ids),
                NightStat.night_id.in_([out.night_id, darkest_night]),
            )
        )
    } if ids else {}  # fmt: skip
    totals = dict(
        (
            await db.execute(
                select(NightStat.device_id, func.sum(NightStat.meteors))
                .where(NightStat.device_id.in_(ids))
                .group_by(NightStat.device_id)
            )
        ).all()
    ) if ids else {}  # fmt: skip
    month = now - dt.timedelta(days=30)
    pairs = await _network_pairs(db, month)
    out.fireballs_month = len(pairs)
    since_evening = now - dt.timedelta(hours=18)
    out.tonight = [await _fireball(db, p, None) for p in pairs if p.at >= since_evening]
    for device in devices:
        credit = public.credit(device, None)
        active = _active(device, now)
        out.cameras += device.kind == "camera"
        out.stations += device.kind == "station"
        out.active += active
        tonight = stats.get((device.id, out.night_id))
        if tonight is not None:
            out.meteors_tonight += tonight.meteors
            for name, count in (tonight.showers or {}).items():
                out.showers_tonight[name] = out.showers_tonight.get(name, 0) + count
        dark = stats.get((device.id, darkest_night))
        if (
            dark is not None
            and dark.best_sqm is not None
            and public.profile(device).sections.sky
            and (dark.clear_hours is None or dark.clear_hours >= SQM_CLEAR_HOURS)
        ):
            out.darkest.append(Ranked(str(credit["name"]), credit["link"], device.kind,
                                      round(dark.best_sqm, 2)))  # fmt: skip
        involved = sum(1 for p in pairs if device.id in _cameras(p))
        since = device.terms_accepted_at if device.kind == "station" else device.paired_at
        out.members.append(
            Member(
                name=str(credit["name"]), link=credit["link"], kind=device.kind,
                software=device.software, since=since, active=active,
                meteors=int(totals.get(device.id) or 0), fireballs=involved,
            )
        )  # fmt: skip
        coords = _shown_place(device)
        if coords is not None:
            out.points.append(
                Point(
                    lat=coords[0], lon=coords[1], title=str(credit["name"]),
                    slug=device.public_slug, kind=device.kind, active=active,
                    image=device.latest_image_at is not None
                    and device.public_slug is not None
                    and public.profile(device).sections.live,
                )
            )  # fmt: skip
    out.darkest.sort(key=lambda r: -r.sqm)
    out.members.sort(key=lambda m: (not m.active, -m.meteors, m.name.lower()))
    return out


class OverviewCache:
    """/netz and the app's network for everyone, without login: computed at most once per
    ``ttl`` (the answers' Cache-Control), so anonymous visitors cannot load the database."""

    def __init__(self, ttl: dt.timedelta = dt.timedelta(seconds=60)) -> None:
        self._ttl = ttl
        self._at: dt.datetime | None = None
        self._view: Overview | None = None

    async def get(self, db: AsyncSession, now: dt.datetime) -> Overview:
        if self._view is None or self._at is None or now - self._at >= self._ttl:
            self._view = await overview(db, now)
            self._at = now
        return self._view


def _shown_place(device: Device) -> tuple[float, float] | None:
    """A station is on the map by its owner's choice even without a page; a camera, as on
    /karte, with its page."""
    if device.kind == "camera" and device.public_slug is None:
        return None
    return public.coordinates(device)
