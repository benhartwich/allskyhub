"""Satellites 3D (draft D, Benjamin 2026-11-15): the bright satellites of the catalogue
around the Earth for one camera, as the page's globe draws them (static/sat_globe.js).

Everything is in the Earth-fixed frame (ECEF) in Earth radii: the satellites now, the paths
of those that are above the camera's horizon within ``WINDOW`` of now, the camera's place
and the sun's direction (for day and night on the globe). Only for the camera's own viewers:
it shows the exact place (web/templating.sky_layers).
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, cast

import numpy as np
import sgp4.api  # pyright: ignore[reportMissingTypeStubs]

from allskyhub_server.devices import astro, bright_satellites
from allskyhub_server.devices.satellite_match import CHUNK, Catalogue, look_all
from allskyhub_server.devices.satellites import EARTH_RADIUS_KM, MIN_ALTITUDE, SKY_DARK, sun_unit

WINDOW = dt.timedelta(minutes=45)
STEP = dt.timedelta(minutes=3)
_SGP4: Any = cast(Any, sgp4.api)


def _gmst(t: dt.datetime) -> float:
    d = (t - dt.datetime(2000, 1, 1, 12, tzinfo=dt.UTC)).total_seconds() / 86400
    return math.radians((280.46061837 + 360.98564736629 * d) % 360)


def ecef(
    array: Any, count: int, times: list[dt.datetime]
) -> tuple[np.ndarray[Any, np.dtype[np.bool_]], np.ndarray[Any, np.dtype[np.float64]]]:
    """Every satellite at every time in the Earth-fixed frame, km: (ok, xyz) of shapes
    (count, steps) and (count, steps, 3). TEME turned by the sidereal angle, as look_all."""
    sgp = _SGP4
    steps = len(times)
    jd = np.empty(steps)
    fr = np.empty(steps)
    for i, t in enumerate(times):
        jd[i], fr[i] = sgp.jday(
            t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6
        )
    r = np.empty((count, steps, 3))
    ok = np.empty((count, steps), dtype=bool)
    for i in range(0, steps, CHUNK):
        err, part, _ = array.sgp4(jd[i : i + CHUNK], fr[i : i + CHUNK])
        r[:, i : i + CHUNK, :] = np.asarray(part, dtype=np.float64)
        ok[:, i : i + CHUNK] = np.asarray(err) == 0
    out = np.empty_like(r)
    for j, t in enumerate(times):
        g = _gmst(t)
        out[:, j, 0] = r[:, j, 0] * math.cos(g) + r[:, j, 1] * math.sin(g)
        out[:, j, 1] = -r[:, j, 0] * math.sin(g) + r[:, j, 1] * math.cos(g)
        out[:, j, 2] = r[:, j, 2]
    return ok, out


def _point(v: Any) -> list[float]:
    return [round(float(c) / EARTH_RADIUS_KM, 4) for c in v]


def _height(range_km: float, altitude: float) -> float:
    """Height above the ground from the slant range and the altitude angle, as
    bright_satellites (spherical Earth, observer at sea level)."""
    r = EARTH_RADIUS_KM
    e = math.radians(altitude)
    return math.sqrt(r * r + range_km * range_km + 2 * r * range_km * math.sin(e)) - r


def sun_ecef(at: dt.datetime) -> list[float]:
    x, y, z = sun_unit(at)
    g = _gmst(at)
    return [
        round(x * math.cos(g) + y * math.sin(g), 4),
        round(-x * math.sin(g) + y * math.cos(g), 4),
        round(z, 4),
    ]


def camera_ecef(lat: float, lon: float) -> list[float]:
    la, lo = math.radians(lat), math.radians(lon)
    return [round(math.cos(la) * math.cos(lo), 5), round(math.cos(la) * math.sin(lo), 5),
            round(math.sin(la), 5)]  # fmt: skip


def globe(catalogue: Catalogue | None, lat: float, lon: float, at: dt.datetime) -> dict[str, Any]:
    """What the globe draws at ``at`` (JSON)."""
    base: dict[str, Any] = {
        "at": at.isoformat(),
        "camera": camera_ecef(lat, lon),
        "sun": sun_ecef(at),
        "satellites": [],
        # False while CelesTrak's elements are missing (the catalogue retries within the
        # hour): the page says so instead of "loading".
        "loaded": catalogue is not None,
    }
    if catalogue is None or catalogue.array is None or not len(catalogue):
        return base
    steps = int(WINDOW / STEP)
    times = [at + STEP * k for k in range(-steps, steps + 1)]
    now_index = steps
    n = len(catalogue)
    ok, xyz = ecef(catalogue.array, n, times)
    # One propagation for everything: the window, and "now" from its middle step (as
    # bright_satellites.now, without computing it again; allsky review of #464).
    good, az, alt, dist, lit = look_all(catalogue.array, n, lat, lon, times)
    dark = astro.sun_altitude(at, lat, lon) < SKY_DARK
    out: list[dict[str, Any]] = []
    for k in range(n):
        if not ok[k, now_index]:
            continue
        norad = catalogue.norads[k]
        a, sunlit = float(alt[k, now_index]), bool(lit[k, now_index])
        up = bool(good[k, now_index]) and a >= 0
        up_in_window = any(bool(good[k, j]) and float(alt[k, j]) > 0 for j in range(len(times)))
        track = [_point(xyz[k, j]) for j in range(len(times)) if ok[k, j]] if up_in_window else []
        out.append({
            "norad": norad,
            "name": bright_satellites.german_name(norad, catalogue.names[k]),
            "now": _point(xyz[k, now_index]),
            "track": track,
            "up": up,
            "altitude": round(a, 1) if up else None,
            "azimuth": round(float(az[k, now_index])) if up else None,
            "height_km": round(_height(float(dist[k, now_index]), a)) if up else None,
            "sunlit": sunlit,
            "naked_eye": up and sunlit and dark and a >= MIN_ALTITUDE,
        })  # fmt: skip
    base["satellites"] = out
    return base
