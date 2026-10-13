"""Satellites among the meteor events (roadmap H6, SPEC §7.9).

The hub loads the orbital elements of CelesTrak's groups ``active`` and ``visual`` once a
day (kept on disk, so a restart does not fetch again) and, for a meteor event with both
track ends, propagates every satellite with SGP4 over ``start .. end`` at least once a
second, all at once (``SatrecArray``, about 16 000 objects in some 50 ms). Those above 5°
and in sunlight (the Earth's shadow as a cylinder) for part of that time are candidates; a
candidate is the streak when both track ends lie within 1° of its apparent path and the
streak is no longer than that path plus 2°.

Frames: TEME is turned into the Earth-fixed frame with Greenwich mean sidereal time only;
nutation and polar motion are far below the 1° of the match.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import itertools
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import numpy as np
import sgp4.api  # pyright: ignore[reportMissingTypeStubs]

from allskyhub_server.devices.aircraft import path_distance, unit
from allskyhub_server.devices.satellites import (  # pyright: ignore[reportPrivateUsage]
    EARTH_RADIUS_KM,
    sun_unit,
)

log = logging.getLogger(__name__)

GROUPS = ("active", "visual")
GP_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=TLE"
MAX_AGE_S = 86400.0
RETRY_S = 3600.0
TIMEOUT_S = 30.0
MIN_ALTITUDE = 5.0
MATCH_DEG = 1.0
LONGER_DEG = 2.0
STEP_S = 1.0
# The fastest a satellite crosses the sky: about 2.8°/s overhead at 160 km.
FASTEST_DEG_S = 3.0
COARSE_S = 10.0
CHUNK = 60
_F = 1 / 298.257223563
_SGP4: Any = cast(Any, sgp4.api)


@dataclass(frozen=True)
class SatMatch:
    name: str
    norad: int
    distance_km: float

    @property
    def detail(self) -> str:
        """SPEC §7.9: name and NORAD number; the two stations by their name."""
        if self.name.startswith("ISS (") or self.norad == 25544:
            return "ISS"
        if self.name.startswith("CSS (") or self.norad == 48274:
            return "Tiangong"
        return f"{self.name} {self.norad}"[:100]


@dataclass(frozen=True)
class Catalogue:
    names: list[str]
    norads: list[int]
    array: Any  # sgp4.api.SatrecArray
    sats: list[Any]  # the same as Satrec objects, for a subset

    def __len__(self) -> int:
        return len(self.names)


def parse(text: str) -> list[tuple[str, int, str, str]]:
    """Three-line TLE text → (name, NORAD number, line 1, line 2)."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    out: list[tuple[str, int, str, str]] = []
    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            with contextlib.suppress(ValueError):
                out.append((name.strip(), int(l1[2:7]), l1, l2))
            i += 3
        else:
            i += 1
    return out


def build(entries: list[tuple[str, int, str, str]]) -> Catalogue:
    seen: set[int] = set()
    names: list[str] = []
    norads: list[int] = []
    sats: list[Any] = []
    for name, norad, l1, l2 in entries:
        if norad in seen:
            continue
        try:
            sat = _SGP4.Satrec.twoline2rv(l1, l2)
        except ValueError:
            continue
        seen.add(norad)
        names.append(name)
        norads.append(norad)
        sats.append(sat)
    return Catalogue(names, norads, _SGP4.SatrecArray(sats) if sats else None, sats)


def _observer(lat: float, lon: float) -> np.ndarray[Any, np.dtype[np.float64]]:
    la, lo = math.radians(lat), math.radians(lon)
    e2 = _F * (2 - _F)
    n = EARTH_RADIUS_KM / math.sqrt(1 - e2 * math.sin(la) ** 2)
    out = np.empty(3)
    out[0] = n * math.cos(la) * math.cos(lo)
    out[1] = n * math.cos(la) * math.sin(lo)
    out[2] = n * (1 - e2) * math.sin(la)
    return out


def _gmst(t: dt.datetime) -> float:
    d = (t - dt.datetime(2000, 1, 1, 12, tzinfo=dt.UTC)).total_seconds() / 86400
    return math.radians((280.46061837 + 360.98564736629 * d) % 360)


def _jday(t: dt.datetime) -> tuple[float, float]:
    return _SGP4.jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)


def match(
    catalogue: Catalogue,
    lat: float,
    lon: float,
    start: dt.datetime,
    end: dt.datetime,
    track_ends: tuple[tuple[float, float], tuple[float, float]],
) -> SatMatch | None:
    """The nearest satellite that is the streak (altitude, azimuth of both ends), or None.

    SGP4 holds the GIL for a whole call, and a meteor event can last minutes. So the
    catalogue is first propagated every ``COARSE_S`` (one short call each): only a
    satellite that comes near one of the streak's ends there can be within ``MATCH_DEG``
    of it in between, and only those are followed second by second."""
    if catalogue.array is None or end < start:
        return None
    seconds = (end - start).total_seconds()
    coarse = max(1, math.ceil(seconds / COARSE_S))
    times = [start + (end - start) * (i / coarse) for i in range(coarse + 1)]
    radius = MATCH_DEG + FASTEST_DEG_S * COARSE_S / 2 + 2.0
    cos_radius = math.cos(math.radians(radius))
    obs = _observer(lat, lon)
    la, lo = math.radians(lat), math.radians(lon)
    ends = [unit(*t) for t in track_ends]
    near = np.zeros(len(catalogue.sats), dtype=bool)
    for t in times:
        a, b = _jday(t)
        err, r, _ = catalogue.array.sgp4(np.array([a]), np.array([b]))
        r = np.asarray(r, dtype=np.float64)[:, 0, :]
        g = _gmst(t)
        x = r[:, 0] * math.cos(g) + r[:, 1] * math.sin(g) - obs[0]
        y = -r[:, 0] * math.sin(g) + r[:, 1] * math.cos(g) - obs[1]
        z = r[:, 2] - obs[2]
        e = -math.sin(lo) * x + math.cos(lo) * y
        n = -math.sin(la) * math.cos(lo) * x - math.sin(la) * math.sin(lo) * y + math.cos(la) * z
        u = math.cos(la) * math.cos(lo) * x + math.cos(la) * math.sin(lo) * y + math.sin(la) * z
        norm = np.sqrt(e * e + n * n + u * u)
        norm[norm == 0] = 1.0
        ok = (np.asarray(err) == 0)[:, 0]
        for p in ends:
            near |= ok & ((e * p[0] + n * p[1] + u * p[2]) / norm >= cos_radius)
    picked = np.nonzero(near)[0].tolist()
    if not picked:
        return None
    return _followed(
        _SGP4.SatrecArray([catalogue.sats[i] for i in picked]),
        [catalogue.names[i] for i in picked],
        [catalogue.norads[i] for i in picked],
        lat, lon, start, end, track_ends,
    )  # fmt: skip


def _followed(
    array: Any,
    names: list[str],
    norads: list[int],
    lat: float,
    lon: float,
    start: dt.datetime,
    end: dt.datetime,
    track_ends: tuple[tuple[float, float], tuple[float, float]],
) -> SatMatch | None:
    """``match`` for the satellites near the streak: every second from start to end."""
    seconds = (end - start).total_seconds()
    steps = max(2, math.ceil(seconds / STEP_S) + 1)
    times = [start + dt.timedelta(seconds=seconds * i / (steps - 1)) for i in range(steps)]
    jd = np.empty(steps)
    fr = np.empty(steps)
    for i, t in enumerate(times):
        a, b = _SGP4.jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)
        jd[i], fr[i] = a, b
    # (n, steps), (n, steps, 3) TEME km; a call per ``CHUNK`` steps, so the GIL comes free.
    r = np.empty((len(names), steps, 3))
    ok = np.empty((len(names), steps), dtype=bool)
    for i in range(0, steps, CHUNK):
        err, part, _ = array.sgp4(jd[i : i + CHUNK], fr[i : i + CHUNK])
        r[:, i : i + CHUNK, :] = np.asarray(part, dtype=np.float64)
        ok[:, i : i + CHUNK] = np.asarray(err) == 0

    obs = _observer(lat, lon)
    la, lo = math.radians(lat), math.radians(lon)
    east_v = (-math.sin(lo), math.cos(lo), 0.0)
    north_v = (-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la))
    up_v = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
    alt = np.empty(r.shape[:2])
    az = np.empty(r.shape[:2])
    dist = np.empty(r.shape[:2])
    lit = np.empty(r.shape[:2], dtype=bool)
    for j, t in enumerate(times):
        g = _gmst(t)
        x = r[:, j, 0] * math.cos(g) + r[:, j, 1] * math.sin(g) - obs[0]
        y = -r[:, j, 0] * math.sin(g) + r[:, j, 1] * math.cos(g) - obs[1]
        z = r[:, j, 2] - obs[2]
        e = east_v[0] * x + east_v[1] * y
        n = north_v[0] * x + north_v[1] * y + north_v[2] * z
        u = up_v[0] * x + up_v[1] * y + up_v[2] * z
        alt[:, j] = np.degrees(np.arctan2(u, np.hypot(e, n)))
        az[:, j] = np.degrees(np.arctan2(e, n)) % 360.0
        dist[:, j] = np.sqrt(x * x + y * y + z * z)
        sun = sun_unit(t)
        along = r[:, j, 0] * sun[0] + r[:, j, 1] * sun[1] + r[:, j, 2] * sun[2]
        perp2 = np.sum(r[:, j, :] ** 2, axis=1) - along * along
        lit[:, j] = (along > 0) | (perp2 > EARTH_RADIUS_KM**2)

    visible = ok & (alt >= MIN_ALTITUDE) & lit
    candidates = np.nonzero(visible.any(axis=1))[0]
    ends = [unit(*e) for e in track_ends]
    streak = math.degrees(
        math.acos(max(-1.0, min(1.0, sum(p * q for p, q in zip(*ends, strict=True)))))
    )
    best: SatMatch | None = None
    for k in candidates.tolist():
        path = [unit(float(alt[k, j]), float(az[k, j])) for j in range(steps) if ok[k, j]]
        if len(path) < 2:
            continue
        length = sum(
            math.degrees(
                math.acos(max(-1.0, min(1.0, sum(p * q for p, q in zip(a, b, strict=True)))))
            )
            for a, b in itertools.pairwise(path)
        )
        if streak > length + LONGER_DEG:
            continue
        if all(path_distance(e, path) <= MATCH_DEG for e in ends):
            km = float(np.min(dist[k][ok[k]]))
            if best is None or km < best.distance_km:
                best = SatMatch(names[k], norads[k], km)
    return best


class SatelliteCatalogue:
    """CelesTrak's ``active`` and ``visual`` groups, fetched once a day, kept on disk."""

    def __init__(self, root: Path, client: httpx.AsyncClient | None) -> None:
        self._root = root
        self._client = client
        self._catalogue: Catalogue | None = None
        self._at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> Catalogue | None:
        if self._catalogue is not None and time.monotonic() - self._at < MAX_AGE_S:
            return self._catalogue
        async with self._lock:
            if self._catalogue is not None and time.monotonic() - self._at < MAX_AGE_S:
                return self._catalogue
            texts: list[str] = []
            for group in GROUPS:
                text = await self._group(group)
                if text:
                    texts.append(text)
            if texts:
                entries = [e for text in texts for e in parse(text)]
                self._catalogue = await asyncio.to_thread(build, entries)
                self._at = time.monotonic()
            else:
                self._at = time.monotonic() - MAX_AGE_S + RETRY_S  # try again in an hour
            return self._catalogue

    async def _group(self, group: str) -> str | None:
        path = self._root / f"{group}.tle"
        try:
            fresh = time.time() - path.stat().st_mtime < MAX_AGE_S
        except OSError:
            fresh = False
        if fresh or self._client is None:
            try:
                return path.read_text()
            except OSError:
                return None
        try:
            r = await self._client.get(GP_URL.format(group=group), timeout=TIMEOUT_S)
            r.raise_for_status()
            text = r.text
            if not parse(text):
                raise ValueError("no elements")
        except (httpx.HTTPError, ValueError) as exc:
            log.info("CelesTrak failed", extra={"group": group, "error": str(exc)})
            try:
                return path.read_text()  # yesterday's elements beat none
            except OSError:
                return None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(path)
        return text
