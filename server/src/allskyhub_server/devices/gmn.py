"""Meteors the Global Meteor Network measured too (fireball package D, Benjamin 2026-11-05).

GMN's video cameras give what one long exposure cannot: the exact time, the speed and so
the orbit. Every ``EVERY`` the hub reads GMN's trajectory summaries of today and yesterday
(``traj_summary_latest_daily.txt``, ``traj_summary_yesterday.txt``; CC BY 4.0, credit
"Global Meteor Network" with a link) and looks for each trajectory among the meteors of
our cameras: the GMN time lies within the event's exposure, and both ends of the camera's
track lie close to the great circle GMN's path spans as seen from the camera, within its
stretch. Only the camera's own data and GMN's public data meet, so any camera may match.

A file is ``;``-separated, one trajectory per line, after three ``#`` header lines (names,
units, dashes); the columns are found by their names, so new ones do not shift them.
"""

from __future__ import annotations

import asyncio
import bisect
import datetime as dt
import logging
import math
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import events, meteor_pairs, triangulation
from allskyhub_server.models import Device, EventRecord, GmnMeteor

log = logging.getLogger(__name__)

BASE = "https://globalmeteornetwork.org/data/traj_summary_data/daily/"
FILES = ("traj_summary_latest_daily.txt", "traj_summary_yesterday.txt")
SOURCE = "https://globalmeteornetwork.org/data/"
EVERY = dt.timedelta(hours=6)  # GMN updates its summaries every six hours
TIMEOUT_S = 60.0
# GMN's time is the meteor's begin to a few milliseconds; ours is the exposure.
SLACK = dt.timedelta(seconds=2)
# How far the camera's track ends may lie off GMN's path (plate solve, SPEC §4.8 ≤ 0.6°,
# and the ends of a long exposure's streak), and beyond its ends (GMN's cameras may see
# less of it, or more).
MATCH_DEG = 2.0
BEYOND = 0.3

# The IAU codes of the showers people ask about; others are shown as their code.
SHOWERS = {
    "QUA": "Quadrantiden", "LYR": "Lyriden", "ETA": "Eta-Aquariiden",
    "SDA": "Südliche Delta-Aquariiden", "CAP": "Alpha-Capricorniden", "PER": "Perseiden",
    "KCG": "Kappa-Cygniden", "DRA": "Draconiden", "ORI": "Orioniden",
    "STA": "Südliche Tauriden", "NTA": "Nördliche Tauriden", "LEO": "Leoniden",
    "GEM": "Geminiden", "URS": "Ursiden", "MON": "Monocerotiden", "HYD": "Sigma-Hydriden",
}  # fmt: skip


def shower_name(code: str | None) -> str:
    if not code:
        return "sporadisch (kein Strom)"
    return SHOWERS.get(code, code)


# (name in the header, how many times it came before): GMN repeats some names.
_COLUMNS = {
    "id": ("Unique trajectory", 0),
    "utc": ("Beginning", 1),
    "shower": ("IAU", 1),
    "a_au": ("a", 0),
    "e": ("e", 0),
    "i_deg": ("i", 0),
    "q_au": ("q", 0),
    "v_init_kms": ("Vinit", 0),
    "v_avg_kms": ("Vavg", 0),
    "beg_lat": ("LatBeg", 0),
    "beg_lon": ("LonBeg", 0),
    "beg_km": ("HtBeg", 0),
    "end_lat": ("LatEnd", 0),
    "end_lon": ("LonEnd", 0),
    "end_km": ("HtEnd", 0),
    "duration_s": ("Duration", 0),
    "peak_mag": ("Peak", 0),
    "mass_kg": ("Mass kg", 0),
    "stations": ("Num", 0),
}


@dataclass(frozen=True)
class Trajectory:
    id: str
    at: dt.datetime
    shower: str | None  # IAU code, e.g. "TAR"; None when sporadic
    v_init_kms: float
    v_avg_kms: float | None
    beg: tuple[float, float, float]  # lat, lon, km
    end: tuple[float, float, float]
    duration_s: float | None
    peak_mag: float | None
    mass_kg: float | None
    stations: int
    a_au: float | None
    e: float | None
    i_deg: float | None
    q_au: float | None


def _positions(names: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for key, (name, skip) in _COLUMNS.items():
        hits = [i for i, n in enumerate(names) if n == name]
        if len(hits) > skip:
            found[key] = hits[skip]
    return found


def _number(cells: list[str], at: int | None) -> float | None:
    if at is None or at >= len(cells):
        return None
    try:
        value = float(cells[at])
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse(text: str) -> list[Trajectory]:
    """The trajectories of one summary file; lines it cannot read are skipped."""
    lines = text.splitlines()
    header = [line for line in lines[:6] if line.startswith("#")]
    if len(header) < 2:
        return []
    columns = _positions([c.strip() for c in header[1].lstrip("#").split(";")])
    if any(k not in columns for k in ("id", "utc", "beg_lat", "end_lat", "v_init_kms")):
        log.warning("gmn: unknown summary columns")
        return []
    out: list[Trajectory] = []
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        cells = [c.strip() for c in line.split(";")]
        try:
            at = dt.datetime.fromisoformat(cells[columns["utc"]]).replace(tzinfo=dt.UTC)
        except (IndexError, ValueError):
            continue
        n = {key: _number(cells, columns.get(key)) for key in _COLUMNS}
        ends = [n[k] for k in ("beg_lat", "beg_lon", "beg_km", "end_lat", "end_lon", "end_km")]
        speed = n["v_init_kms"]
        points = [v for v in ends if v is not None]
        if speed is None or len(points) != len(ends):
            continue
        beg, end = (points[0], points[1], points[2]), (points[3], points[4], points[5])
        shower = cells[columns["shower"]] if "shower" in columns else ""
        out.append(Trajectory(
            id=cells[columns["id"]][:40], at=at,
            shower=shower if shower.isalpha() and len(shower) <= 6 else None,
            v_init_kms=speed, v_avg_kms=n["v_avg_kms"], beg=beg, end=end,
            duration_s=n["duration_s"], peak_mag=n["peak_mag"], mass_kg=n["mass_kg"],
            stations=int(n["stations"] or 0),
            a_au=n["a_au"], e=n["e"], i_deg=n["i_deg"], q_au=n["q_au"],
        ))  # fmt: skip
    return out


Vec = triangulation.Vec


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(a: Vec) -> Vec:
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def _toward(o: Vec, p: Vec) -> Vec:
    return _unit((p[0] - o[0], p[1] - o[1], p[2] - o[2]))


def _angle(a: Vec, b: Vec) -> float:
    return math.acos(max(-1.0, min(1.0, _dot(a, b))))


def offset_deg(s: triangulation.Sighting, t: Trajectory) -> float | None:
    """How far the camera's track ends lie off GMN's path seen from the camera (the worse
    of both, degrees); None when they do not fall within its stretch or it is below the
    horizon."""
    o = triangulation.ecef(s.lat, s.lon, s.height_km)
    b, e = triangulation.ecef(*t.beg), triangulation.ecef(*t.end)
    if max(triangulation.look(s.lat, s.lon, s.height_km, p)[1] for p in (b, e)) <= 0:
        return None
    vb, ve = _toward(o, b), _toward(o, e)
    n = (
        vb[1] * ve[2] - vb[2] * ve[1],
        vb[2] * ve[0] - vb[0] * ve[2],
        vb[0] * ve[1] - vb[1] * ve[0],
    )
    if _dot(n, n) < 1e-18:
        return None
    normal = _unit(n)
    span = _angle(vb, ve)
    worst = 0.0
    for azimuth, altitude in (s.end1, s.end2):
        p = triangulation.direction(s.lat, s.lon, azimuth, altitude)
        # Within GMN's stretch (a little beyond its ends): neither end farther than that.
        if max(_angle(p, vb), _angle(p, ve)) > span * (1 + BEYOND) + math.radians(MATCH_DEG):
            return None
        worst = max(worst, math.degrees(math.asin(min(1.0, abs(_dot(p, normal))))))
    return worst


def _values(t: Trajectory, offset: float) -> dict[str, Any]:
    return {
        "gmn_id": t.id, "at": t.at, "shower": t.shower,
        "v_init_kms": t.v_init_kms, "v_avg_kms": t.v_avg_kms,
        "beg_lat": t.beg[0], "beg_lon": t.beg[1], "beg_km": t.beg[2],
        "end_lat": t.end[0], "end_lon": t.end[1], "end_km": t.end[2],
        "duration_s": t.duration_s, "peak_mag": t.peak_mag, "mass_kg": t.mass_kg,
        "stations": t.stations, "a_au": t.a_au, "e": t.e, "i_deg": t.i_deg, "q_au": t.q_au,
        "offset_deg": offset,
    }  # fmt: skip


async def match(db: AsyncSession, trajectories: list[Trajectory]) -> int:
    """Keep each of our meteors' best GMN trajectory; returns how many were stored."""
    if not trajectories:
        return 0
    first = min(t.at for t in trajectories) - SLACK
    last = max(t.at for t in trajectories) + SLACK
    rows = (
        await db.execute(
            select(EventRecord, Device)
            .join(Device, Device.id == EventRecord.device_id)
            .where(
                EventRecord.kind == "meteor",
                EventRecord.end >= first,
                EventRecord.start <= last,
                events.counted(),
            )
        )
    ).all()
    ordered = sorted(trajectories, key=lambda t: t.at)
    times = [t.at for t in ordered]
    stored = 0
    for event, device in rows:
        seen = meteor_pairs.sighting(device, event)
        if seen is None:
            continue
        best: tuple[float, Trajectory] | None = None
        lo = bisect.bisect_left(times, event.start - SLACK)
        hi = bisect.bisect_right(times, event.end + SLACK)
        for t in ordered[lo:hi]:
            off = offset_deg(seen, t)
            if off is not None and off <= MATCH_DEG and (best is None or off < best[0]):
                best = (off, t)
        if best is None:
            continue
        values = _values(best[1], best[0])
        await db.execute(
            pg_insert(GmnMeteor)
            .values(device_id=device.id, event_id=event.event_id, **values)
            .on_conflict_do_update(index_elements=["device_id", "event_id"], set_=values)
        )
        stored += 1
    await db.commit()
    return stored


async def for_events(db: AsyncSession, device_id: str, event_ids: list[str]) -> list[GmnMeteor]:
    if not event_ids:
        return []
    found = await db.scalars(
        select(GmnMeteor).where(GmnMeteor.device_id == device_id, GmnMeteor.event_id.in_(event_ids))
    )
    return list(found)


async def fetch(client: httpx.AsyncClient) -> list[Trajectory]:
    out: list[Trajectory] = []
    for name in FILES:
        r = await client.get(BASE + name, timeout=TIMEOUT_S)
        r.raise_for_status()
        out.extend(parse(r.text))
    return out


async def run_forever(maker: async_sessionmaker[AsyncSession], client: httpx.AsyncClient) -> None:
    while True:
        try:
            trajectories = await fetch(client)
            async with maker() as db:
                stored = await match(db, trajectories)
            log.info("gmn: %d trajectories, %d meteors matched", len(trajectories), stored)
        except Exception:  # GMN unreachable or a bad file: the next round tries again
            log.exception("gmn round failed")
        await asyncio.sleep(EVERY.total_seconds())
