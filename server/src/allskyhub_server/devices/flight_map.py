"""The aircraft around a camera on a map (draft 7, 2026-11-13), with the area where an
aircraft at cruising height shows in the camera's picture.

The aircraft are the ones the hub already follows for the live overlay and the meteors
(``aircraft.Tracks``, SPEC §7.8): adsb.lol and the camera's own receiver. Nothing new is
fetched; the page marks the camera as watched, like the public page.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from allskyhub_server.devices.aircraft import RADIUS_KM, Tracks, look

CRUISE_M = 10_000.0
# The rings: from this far an aircraft at CRUISE_M is at least that many degrees up.
RING_ELEVATIONS = (10.0, 30.0)
IN_PICTURE = 10.0  # degrees up: where an all-sky camera still sees sky
TRAIL = dt.timedelta(minutes=2)
TRAIL_STEP = dt.timedelta(seconds=15)
MAX_PLANES = 150


@dataclass(frozen=True)
class Ring:
    elevation: float
    km: float


def ring_km(lat: float, lon: float, elevation: float, alt_m: float = CRUISE_M) -> float:
    """Ground distance at which an aircraft at ``alt_m`` stands ``elevation`` degrees up
    (Earth's curvature included), found by bisection along the meridian."""
    lo, hi = 0.0, 1000.0
    for _ in range(40):
        mid = (lo + hi) / 2
        north = lat + mid / 111.195
        up, _, _ = look(lat, lon, north, lon, alt_m)
        if up > elevation:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 1)


@dataclass(frozen=True)
class MapPlane:
    hex: str
    name: str
    type_code: str | None
    alt_m: float
    lat: float
    lon: float
    track_deg: float | None
    elevation: float
    azimuth: float
    distance_km: float
    trail: list[tuple[float, float]] = field(default_factory=lambda: list[tuple[float, float]]())

    @property
    def in_picture(self) -> bool:
        return self.elevation >= IN_PICTURE


def planes(
    tracks: Tracks, lat: float, lon: float, now: dt.datetime, device_id: str = ""
) -> list[MapPlane]:
    """The aircraft within ``RADIUS_KM`` of the camera at ``now``, those in the picture
    first, then the nearest; each with its trail of the last ``TRAIL``."""
    past: list[dict[str, tuple[float, float]]] = []
    t = now - TRAIL
    while t < now:
        past.append({k: (f.lat, f.lon) for k, f in tracks.at(t, device_id).items()})
        t += TRAIL_STEP
    found: list[MapPlane] = []
    for key, fix in tracks.at(now, device_id).items():
        if fix.alt_m is None:
            continue
        elevation, azimuth, km = look(lat, lon, fix.lat, fix.lon, fix.alt_m)
        if km > RADIUS_KM:
            continue
        trail = [p[key] for p in past if key in p]
        _, type_code = tracks.info.get(key, (None, None))
        found.append(
            MapPlane(
                hex=key,
                name=fix.flight or key,
                type_code=type_code,
                alt_m=fix.alt_m,
                lat=fix.lat,
                lon=fix.lon,
                track_deg=fix.track_deg,
                elevation=elevation,
                azimuth=azimuth,
                distance_km=km,
                trail=[*trail, (fix.lat, fix.lon)],
            )
        )
    found.sort(key=lambda p: (not p.in_picture, p.distance_km))
    return found[:MAX_PLANES]


def rings(lat: float, lon: float) -> list[Ring]:
    return [Ring(e, ring_km(lat, lon, e)) for e in RING_ELEVATIONS]
