"""Aircraft around the cameras (roadmap H3, SPEC §7.8).

The hub fetches the aircraft around each camera that needs them from the open adsb.lol API
(ODbL, no key): online, with a location, and either night there with meteor detection on,
or someone watching its live image. Cameras within ``RADIUS_KM`` of each other share one
request. Positions are kept ``KEEP`` long, only for the live overlay and for matching
meteors; a camera's own receiver (SPEC §6.3 ``aircraft``) adds its positions, which win
per ICAO address while at most ``RECEIVER_WINS`` old.

Geometry: the camera at sea level, a spherical Earth; at the distances that matter (an
aircraft a few degrees up is within 150 km) the error stays well under the 2° of the
match.
"""

from __future__ import annotations

import asyncio
import bisect
import datetime as dt
import itertools
import logging
import math
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.models import Device, EventRecord

if TYPE_CHECKING:
    from allskyhub_server.devices.satellite_match import SatelliteCatalogue

log = logging.getLogger(__name__)

API = "https://api.adsb.lol/v2/point/{lat:.3f}/{lon:.3f}/{nm}"
CREDIT = "Aircraft data: adsb.lol (ODbL)"
FETCH_EVERY = dt.timedelta(seconds=10)
KEEP = dt.timedelta(minutes=15)
RADIUS_KM = 150.0
MAX_NM = 250  # adsb.lol's largest radius
MAX_AGE_S = 60.0  # a position older than this is not taken
RECEIVER_WINS = dt.timedelta(seconds=30)
DEAD_RECKON = dt.timedelta(seconds=30)
# Longer without positions: the window is not covered. adsb.lol times out or answers 429
# now and then; a few failed fetches in a row still leave a straight line between fixes.
FETCH_GAP = dt.timedelta(seconds=60)
# Longer between two fixes: the aircraft may have turned, so no straight line between them.
TURN_GAP = dt.timedelta(minutes=2)
NIGHT_SUN = -6.0
EARTH_KM = 6371.0
FEET = 0.3048
KNOT_KMS = 1.852 / 3600.0
TRAIL = dt.timedelta(minutes=1)

Source = Literal["adsb.lol", "receiver"]


@dataclass(frozen=True)
class Fix:
    """One position report."""

    at: dt.datetime
    lat: float
    lon: float
    alt_m: float | None
    track_deg: float | None
    speed_kn: float | None
    flight: str | None


@dataclass(frozen=True)
class Plane:
    """An aircraft where it is at a moment, as seen from a camera."""

    hex: str
    flight: str | None
    alt_m: float | None
    elevation: float  # degrees above the horizon
    azimuth: float  # degrees from north toward east
    distance_km: float

    @property
    def name(self) -> str:
        return self.flight or self.hex

    @property
    def detail(self) -> str:
        """For the label (SPEC §6.4): call sign and ICAO address."""
        return f"{self.flight} {self.hex}" if self.flight else self.hex


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def _move(lat: float, lon: float, track_deg: float, km: float) -> tuple[float, float]:
    """The point ``km`` along ``track_deg`` from (lat, lon)."""
    d = km / EARTH_KM
    p, lam, b = math.radians(lat), math.radians(lon), math.radians(track_deg)
    p2 = math.asin(math.sin(p) * math.cos(d) + math.cos(p) * math.sin(d) * math.cos(b))
    lam2 = lam + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p), math.cos(d) - math.sin(p) * math.sin(p2)
    )
    return math.degrees(p2), (math.degrees(lam2) + 540.0) % 360.0 - 180.0


def look(
    cam_lat: float, cam_lon: float, lat: float, lon: float, alt_m: float
) -> tuple[float, float, float]:
    """Elevation, azimuth (degrees) and ground distance (km) of a point at ``alt_m`` above
    sea level, seen from the camera at sea level."""

    def vec(la: float, lo: float, r: float) -> tuple[float, float, float]:
        p, q = math.radians(la), math.radians(lo)
        return (r * math.cos(p) * math.cos(q), r * math.cos(p) * math.sin(q), r * math.sin(p))

    c = vec(cam_lat, cam_lon, EARTH_KM)
    a = vec(lat, lon, EARTH_KM + alt_m / 1000.0)
    d = (a[0] - c[0], a[1] - c[1], a[2] - c[2])
    p, q = math.radians(cam_lat), math.radians(cam_lon)
    east = -math.sin(q) * d[0] + math.cos(q) * d[1]
    north = (
        -math.sin(p) * math.cos(q) * d[0] - math.sin(p) * math.sin(q) * d[1] + math.cos(p) * d[2]
    )
    up = math.cos(p) * math.cos(q) * d[0] + math.cos(p) * math.sin(q) * d[1] + math.sin(p) * d[2]
    elevation = math.degrees(math.atan2(up, math.hypot(east, north)))
    azimuth = math.degrees(math.atan2(east, north)) % 360.0
    return elevation, azimuth, _km(cam_lat, cam_lon, lat, lon)


def _between(a: Fix, b: Fix, t: dt.datetime) -> Fix:
    f = (t - a.at) / (b.at - a.at) if b.at > a.at else 0.0
    dlon = (b.lon - a.lon + 540.0) % 360.0 - 180.0
    alt = (
        a.alt_m + (b.alt_m - a.alt_m) * f
        if a.alt_m is not None and b.alt_m is not None
        else a.alt_m or b.alt_m
    )
    return Fix(
        t, a.lat + (b.lat - a.lat) * f, a.lon + dlon * f, alt, b.track_deg, b.speed_kn,
        b.flight or a.flight,
    )  # fmt: skip


def _ahead(fix: Fix, t: dt.datetime) -> Fix | None:
    """Dead reckoning from the newest position, at most ``DEAD_RECKON`` ahead."""
    gap = t - fix.at
    if gap > DEAD_RECKON:
        return None
    if gap <= dt.timedelta(0) or fix.track_deg is None or fix.speed_kn is None:
        return fix if gap <= dt.timedelta(seconds=5) else None
    lat, lon = _move(fix.lat, fix.lon, fix.track_deg, fix.speed_kn * KNOT_KMS * gap.total_seconds())
    return Fix(t, lat, lon, fix.alt_m, fix.track_deg, fix.speed_kn, fix.flight)


@dataclass
class _Track:
    fixes: list[Fix] = field(default_factory=lambda: list[Fix]())

    def add(self, fix: Fix) -> None:
        times = [f.at for f in self.fixes]
        i = bisect.bisect_left(times, fix.at)
        if i < len(self.fixes) and self.fixes[i].at == fix.at:
            return
        self.fixes.insert(i, fix)

    def at(self, t: dt.datetime) -> Fix | None:
        """Interpolated between the positions around ``t``, else dead-reckoned past the
        newest; None before the first or too far after the last."""
        if not self.fixes:
            return None
        times = [f.at for f in self.fixes]
        i = bisect.bisect_left(times, t)
        if i < len(self.fixes) and self.fixes[i].at == t:
            return self.fixes[i]
        if i == 0:
            return None
        if i == len(self.fixes):
            return _ahead(self.fixes[-1], t)
        a, b = self.fixes[i - 1], self.fixes[i]
        if b.at - a.at > TURN_GAP:
            return _ahead(a, t)
        return _between(a, b, t)


class Tracks:
    """Positions per source and ICAO address, kept ``KEEP``; and when each camera's area
    was last covered (adsb.lol fetches and the camera's own relays)."""

    def __init__(self) -> None:
        self._tracks: dict[Source, dict[str, _Track]] = {"adsb.lol": {}, "receiver": {}}
        self._covered: dict[str, list[dt.datetime]] = {}

    def add(self, source: Source, hex_: str, fix: Fix) -> None:
        self._tracks[source].setdefault(hex_.lower(), _Track()).add(fix)

    def covered(self, device_id: str, at: dt.datetime) -> None:
        times = self._covered.setdefault(device_id, [])
        if not times or at > times[-1]:
            times.append(at)

    def prune(self, now: dt.datetime) -> None:
        oldest = now - KEEP
        for tracks in self._tracks.values():
            for key in list(tracks):
                fixes = [f for f in tracks[key].fixes if f.at >= oldest]
                if fixes:
                    tracks[key].fixes = fixes
                else:
                    del tracks[key]
        for key in list(self._covered):
            times = [t for t in self._covered[key] if t >= oldest]
            if times:
                self._covered[key] = times
            else:
                del self._covered[key]

    def covers(self, device_id: str, start: dt.datetime, end: dt.datetime) -> bool:
        """Positions for the whole of ``start .. end``: one fetch at or before ``start``,
        one at or after ``end``, never more than ``FETCH_GAP`` between two."""
        times = self._covered.get(device_id) or []
        inside = [t for t in times if start - FETCH_GAP <= t <= end + FETCH_GAP]
        if not inside or inside[0] > start or inside[-1] < end:
            return False
        return all(b - a <= FETCH_GAP for a, b in itertools.pairwise(inside))

    def at(self, t: dt.datetime) -> dict[str, Fix]:
        """Every aircraft's position at ``t``; the receiver's while it is fresh."""
        out: dict[str, Fix] = {}
        for key, track in self._tracks["adsb.lol"].items():
            fix = track.at(t)
            if fix is not None:
                out[key] = fix
        for key, track in self._tracks["receiver"].items():
            fix = track.at(t)
            newest = track.fixes[-1].at if track.fixes else None
            if fix is not None and newest is not None and t - newest <= RECEIVER_WINS:
                out[key] = fix
        return out

    def planes(
        self, t: dt.datetime, lat: float, lon: float, min_elevation: float = 0.0
    ) -> list[Plane]:
        """The aircraft above ``min_elevation`` from a camera at ``t``, nearest first."""
        out: list[Plane] = []
        for key, fix in self.at(t).items():
            if (
                fix.alt_m is None
                or abs(fix.lat - lat) > 3
                or _km(lat, lon, fix.lat, fix.lon) > RADIUS_KM
            ):
                continue
            elevation, azimuth, km = look(lat, lon, fix.lat, fix.lon, fix.alt_m)
            if elevation >= min_elevation:
                out.append(Plane(key, fix.flight, fix.alt_m, elevation, azimuth, km))
        return sorted(out, key=lambda p: p.distance_km)


# --- matching a meteor (SPEC §7.8) ----------------------------------------------------------

MATCH_DEG = 2.0
MATCH_MIN_ELEVATION = 5.0
WINDOW = dt.timedelta(seconds=30)
STEP = dt.timedelta(seconds=1)


def unit(elevation: float, azimuth: float) -> tuple[float, float, float]:
    e, a = math.radians(elevation), math.radians(azimuth)
    return (math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))


def _dot(u: tuple[float, float, float], v: tuple[float, float, float]) -> float:
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def _angle(u: tuple[float, float, float], v: tuple[float, float, float]) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, _dot(u, v)))))


def _to_arc(
    p: tuple[float, float, float], a: tuple[float, float, float], b: tuple[float, float, float]
) -> float:
    """Angular distance (degrees) from ``p`` to the short great-circle arc ``a``–``b``."""
    n = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    norm = math.sqrt(_dot(n, n))
    ends = min(_angle(p, a), _angle(p, b))
    if norm < 1e-12:
        return ends
    n = (n[0] / norm, n[1] / norm, n[2] / norm)
    off = _dot(p, n)
    q = (p[0] - off * n[0], p[1] - off * n[1], p[2] - off * n[2])
    qn = math.sqrt(_dot(q, q))
    if qn < 1e-12:
        return ends
    q = (q[0] / qn, q[1] / qn, q[2] / qn)
    if abs(_angle(a, q) + _angle(q, b) - _angle(a, b)) < 1e-6:  # foot point on the arc
        return math.degrees(math.asin(min(1.0, abs(off))))
    return ends


def path_distance(p: tuple[float, float, float], path: list[tuple[float, float, float]]) -> float:
    if len(path) == 1:
        return _angle(p, path[0])
    return min(_to_arc(p, a, b) for a, b in itertools.pairwise(path))


def match(
    tracks: Tracks,
    lat: float,
    lon: float,
    start: dt.datetime,
    end: dt.datetime,
    track_ends: tuple[tuple[float, float], tuple[float, float]],
) -> Plane | None:
    """The nearest aircraft whose apparent path over ``start - 30 s .. end + 30 s`` passes
    within 2° of both ends of the meteor's track ((altitude, azimuth) each), above 5° for
    part of it; None if there is none."""
    ends = [unit(*e) for e in track_ends]
    paths: dict[str, list[tuple[float, float, float]]] = {}
    seen: dict[str, Plane] = {}
    t = start - WINDOW
    while t <= end + WINDOW:
        for plane in tracks.planes(t, lat, lon, min_elevation=-10.0):
            paths.setdefault(plane.hex, []).append(unit(plane.elevation, plane.azimuth))
            if plane.elevation >= MATCH_MIN_ELEVATION:
                best = seen.get(plane.hex)
                if best is None or plane.distance_km < best.distance_km:
                    seen[plane.hex] = plane
        t += STEP
    found = [
        seen[key]
        for key, path in paths.items()
        if key in seen and all(path_distance(e, path) <= MATCH_DEG for e in ends)
    ]
    return min(found, key=lambda p: p.distance_km) if found else None


# --- fetching (adsb.lol) ---------------------------------------------------------------------

Fetch = Callable[[float, float, int], Awaitable[dict[str, Any]]]


def http_fetch(client: httpx.AsyncClient) -> Fetch:
    async def fetch(lat: float, lon: float, nm: int) -> dict[str, Any]:
        r = await client.get(API.format(lat=lat, lon=lon, nm=nm), timeout=8.0)
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        return body

    return fetch


def parse(body: dict[str, Any], received: dt.datetime) -> Iterable[tuple[str, Fix]]:
    """adsb.lol's ``ac`` list → positions, without ones on the ground or too old."""
    now_ms = body.get("now")
    base = (
        dt.datetime.fromtimestamp(now_ms / 1000.0, dt.UTC)
        if isinstance(now_ms, int | float)
        else received
    )
    entries: object = body.get("ac") or []
    for raw in cast(list[object], entries) if isinstance(entries, list) else []:
        if not isinstance(raw, dict):
            continue
        ac = cast(dict[str, Any], raw)
        hex_, lat, lon = ac.get("hex"), ac.get("lat"), ac.get("lon")
        alt, seen = ac.get("alt_baro"), ac.get("seen_pos", 0.0)
        if (
            not isinstance(hex_, str)
            or not isinstance(lat, int | float)
            or not isinstance(lon, int | float)
        ):
            continue
        if (
            not isinstance(alt, int | float)
            or not isinstance(seen, int | float)
            or seen > MAX_AGE_S
        ):
            continue  # "ground" or no altitude: never in the sky
        flight = ac.get("flight")
        track, speed = ac.get("track"), ac.get("gs")
        yield hex_.lower().lstrip("~"), Fix(
            base - dt.timedelta(seconds=float(seen)),
            float(lat), float(lon), float(alt) * FEET,
            float(track) if isinstance(track, int | float) else None,
            float(speed) if isinstance(speed, int | float) else None,
            flight.strip() or None if isinstance(flight, str) else None,
        )  # fmt: skip


@dataclass(frozen=True)
class Area:
    lat: float
    lon: float
    nm: int
    devices: tuple[str, ...]


def areas(cameras: list[tuple[str, float, float]]) -> list[Area]:
    """Cameras within ``RADIUS_KM`` of a group's centre share its request, whose radius
    covers ``RADIUS_KM`` around each of them. The centre is rounded to 0.1° (about
    10 km), so adsb.lol never learns where exactly a camera stands."""
    groups: list[tuple[float, float, list[tuple[str, float]]]] = []
    for device_id, lat, lon in cameras:
        for g_lat, g_lon, members in groups:
            d = _km(g_lat, g_lon, lat, lon)
            if d <= RADIUS_KM:
                members.append((device_id, d))
                break
        else:
            c_lat, c_lon = round(lat, 1), round(lon, 1)
            groups.append((c_lat, c_lon, [(device_id, _km(c_lat, c_lon, lat, lon))]))
    return [
        Area(
            g_lat,
            g_lon,
            min(MAX_NM, math.ceil((RADIUS_KM + max(d for _, d in members)) / 1.852)),
            tuple(device_id for device_id, _ in members),
        )
        for g_lat, g_lon, members in groups
    ]


async def fetch_areas(
    tracks: Tracks, fetch: Fetch, wanted: list[tuple[str, float, float]], now: dt.datetime
) -> None:
    """One round: every area once; a failed request leaves its cameras uncovered."""
    for area in areas(wanted):
        try:
            body = await fetch(area.lat, area.lon, area.nm)
        except (httpx.HTTPError, ValueError) as exc:
            log.info("adsb.lol failed", extra={"error": str(exc)})
            continue
        for hex_, fix in parse(body, now):
            tracks.add("adsb.lol", hex_, fix)
        for device_id in area.devices:
            tracks.covered(device_id, now)
    tracks.prune(now)


def wants_aircraft(
    status: dict[str, Any] | None, live: bool, now: dt.datetime
) -> tuple[float, float] | None:
    """The camera's place if it needs aircraft now (SPEC §7.8), else None."""
    from allskyhub_sky.astro import sun_altitude

    settings: dict[str, Any] = (status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    if live:
        return float(lat), float(lon)
    meteors = settings.get("detect_meteor", "normal") != "off"
    if meteors and sun_altitude(now, float(lat), float(lon)) < NIGHT_SUN:
        return float(lat), float(lon)
    return None


CHECK_AFTER = dt.timedelta(seconds=60)
WATCH_HOLD = dt.timedelta(minutes=2)
GIVE_UP = dt.timedelta(minutes=3)


def _track_ends(data: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """SPEC §6.4: the meteor's track ends (altitude, azimuth); None without them."""
    keys = ("start_altitude_deg", "start_azimuth_deg", "end_altitude_deg", "end_azimuth_deg")
    values = [data.get(k) for k in keys]
    if not all(isinstance(v, int | float) for v in values):
        return None
    a, b, c, d = (float(v) for v in values)  # pyright: ignore[reportArgumentType]
    return (a, b), (c, d)


def _place(status: dict[str, Any] | None) -> tuple[float, float] | None:
    settings: dict[str, Any] = (status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return float(lat), float(lon)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


current: Watch | None = None


class Watch:
    """The hub's aircraft: fetched every ``FETCH_EVERY`` for the cameras that need them."""

    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        registry: ConnectionRegistry,
        fetch: Fetch | None,
    ) -> None:
        self._maker = maker
        self._registry = registry
        self._fetch = fetch
        self.tracks = Tracks()
        self._checked: dict[tuple[str, str], dt.datetime] = {}
        # Meteors check_new has computed: a camera sends its backlog again after each
        # reconnect, and a long event costs seconds of SGP4.
        self._checked_new: dict[tuple[str, str], dt.datetime] = {}
        self._watched_until: dict[str, dt.datetime] = {}
        # Roadmap H6 (SPEC §7.9): set by the app; meteors are checked against both.
        self.satellites: SatelliteCatalogue | None = None
        global current  # the templates draw from the app's one Watch
        current = self

    def watched(self, device_id: str) -> None:
        """A public page shows the camera's live image (the owner's page marks the
        registry's live view instead, which also fetches every frame)."""
        self._watched_until[device_id] = _now() + WATCH_HOLD

    def _is_watched(self, device_id: str, now: dt.datetime) -> bool:
        if self._registry.is_live(device_id):
            return True
        until = self._watched_until.get(device_id)
        return until is not None and until > now

    async def round(self, now: dt.datetime) -> None:
        if self._fetch is None:
            return
        ids = self._registry.online_ids()
        if not ids:
            return
        async with self._maker() as db:
            rows = (
                await db.execute(select(Device.id, Device.last_status).where(Device.id.in_(ids)))
            ).all()
        wanted: list[tuple[str, float, float]] = []
        for device_id, status in rows:
            place = wants_aircraft(status, self._is_watched(device_id, now), now)
            if place is not None:
                wanted.append((device_id, *place))
        if wanted:
            await fetch_areas(self.tracks, self._fetch, wanted, now)

    def receive(self, device_id: str, at: dt.datetime, positions: Iterable[Any]) -> None:
        """SPEC §6.3 ``aircraft`` from the camera's own receiver."""
        for p in positions:
            fix = Fix(
                at - dt.timedelta(seconds=p.seen_s), p.lat, p.lon, p.alt_m, p.track_deg,
                p.speed_kn, p.flight,
            )  # fmt: skip
            self.tracks.add("receiver", p.hex, fix)
        self.tracks.covered(device_id, at)

    async def check_meteors(self, now: dt.datetime) -> set[str]:
        """SPEC §7.8: every unlabelled, not imported meteor whose window the kept positions
        cover, once ``end + 60 s`` has passed; a match is an automatic false positive.
        Returns the cameras whose labels changed."""
        from allskyhub_server.devices import labels

        for key in [k for k, t in self._checked.items() if t < now - KEEP]:
            del self._checked[key]
        for key in [k for k, t in self._checked_new.items() if t < now - KEEP]:
            del self._checked_new[key]
        changed: set[str] = set()
        async with self._maker() as db:
            rows = (
                await db.execute(
                    select(EventRecord, Device.last_status)
                    .join(Device, Device.id == EventRecord.device_id)
                    .where(
                        EventRecord.kind == "meteor",
                        or_(EventRecord.labelled_at.is_(None), EventRecord.label_auto.is_(True)),
                        EventRecord.end <= now - CHECK_AFTER,
                        EventRecord.end >= now - KEEP + WINDOW,
                    )
                )
            ).all()
            for event, status in rows:
                key = (event.device_id, event.event_id)
                if key in self._checked:
                    continue
                window = (event.start - WINDOW, event.end + WINDOW)
                ends = _track_ends(event.data or {})
                place = _place(status)
                if (event.data or {}).get("imported") or ends is None or place is None:
                    self._checked[key] = now
                    continue
                if not self.tracks.covers(event.device_id, *window):
                    if now - event.end > GIVE_UP:  # no positions came for it
                        self._checked[key] = now
                    continue
                self._checked[key] = now
                found = await self._verdict(event, place, ends, with_aircraft=True)
                if found is not None and await labels.set_auto(db, event, *found, now):
                    log.info(
                        "meteor is no meteor",
                        extra={"device_id": event.device_id, "event": event.event_id,
                               "reason": found[0]},
                    )  # fmt: skip
                    changed.add(event.device_id)
            await db.commit()
        for device_id in changed:
            labels.deliver_later(self._maker, self._registry, device_id)
        return changed

    async def _verdict(
        self,
        event: EventRecord,
        place: tuple[float, float],
        ends: tuple[tuple[float, float], tuple[float, float]],
        *,
        with_aircraft: bool,
    ) -> tuple[str, str] | None:
        """(reason, detail) of the nearest aircraft (§7.8) or satellite (§7.9) that is the
        streak, or None."""
        from allskyhub_server.devices import satellite_match

        best: tuple[float, str, str] | None = None
        if with_aircraft:
            plane = match(self.tracks, *place, event.start, event.end, ends)
            if plane is not None:
                best = (plane.distance_km, "aircraft", plane.detail)
        catalogue = await self.satellites.get() if self.satellites is not None else None
        if catalogue is not None:
            sat = await asyncio.to_thread(
                satellite_match.match, catalogue, *place, event.start, event.end, ends
            )
            if sat is not None and (best is None or sat.distance_km < best[0]):
                best = (sat.distance_km, "satellite", sat.detail)
        return None if best is None else (best[1], best[2])

    async def check_new(self, device_id: str, event_id: str) -> bool:
        """SPEC §7.9: a meteor is checked for satellites when it arrives (also from a
        backlog: orbits can be computed for any time), and for aircraft if the kept
        positions already cover it. True if it got an automatic label."""
        from allskyhub_server.devices import labels

        async with self._maker() as db:
            row = (
                await db.execute(
                    select(EventRecord, Device.last_status)
                    .join(Device, Device.id == EventRecord.device_id)
                    .where(EventRecord.device_id == device_id, EventRecord.event_id == event_id)
                )
            ).one_or_none()
            if row is None:
                return False
            event, status = row
            if event.kind != "meteor" or (event.labelled_at is not None and not event.label_auto):
                return False
            ends = _track_ends(event.data or {})
            place = _place(status)
            if (event.data or {}).get("imported") or ends is None or place is None:
                return False
            covered = self.tracks.covers(device_id, event.start - WINDOW, event.end + WINDOW)
            key = (device_id, event_id)
            if key in self._checked_new and not covered:
                return False  # computed already; only new aircraft positions add to it
            self._checked_new[key] = _now()
            found = await self._verdict(event, place, ends, with_aircraft=covered)
            if found is None or not await labels.set_auto(db, event, *found, _now()):
                return False
            await db.commit()
        labels.deliver_later(self._maker, self._registry, device_id)
        return True

    async def run_forever(self) -> None:
        while True:
            started = _now()
            try:
                await self.round(started)
                await self.check_meteors(started)
            except Exception:  # never let one bad round end the loop
                log.exception("aircraft round failed")
            await asyncio.sleep(max(0.0, (started + FETCH_EVERY - _now()).total_seconds()))
