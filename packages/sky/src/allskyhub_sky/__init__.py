"""Constellations and the cardinal points drawn over a camera's image (roadmap H1, L11).

Shared by the hub (public page, camera page) and the camera's local UI, so both draw the
same sky. No web framework, only the protocol's ``Lens``.

Star positions (J2000, d3-celestial) are moved to the date with the low-precision
precession, turned into altitude and azimuth for the camera's place at the image's time,
and mapped to pixels with the lens the camera fitted when it plate-solved (SPEC §4.8,
§6.3 ``orientation.lens``). Without a lens there is no overlay. Lines below the horizon
are cut; long lines are split so they follow the fisheye's curve.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import math
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from allskyhub_protocol import Lens

DATA = Path(__file__).parent / "data" / "constellations.json"
STEP_DEG = 3.0  # longest straight piece of a line, so it bends like the lens
LABEL_MIN_ALT = 12.0
LABEL_MAX_RANK = 2
CARDINALS = (("N", 0.0), ("O", 90.0), ("S", 180.0), ("W", 270.0))
CARDINAL_ALT = 3.0


@dataclass(frozen=True)
class Label:
    x: float
    y: float
    text: str


@dataclass(frozen=True)
class Overlay:
    width: int
    height: int
    paths: list[str] = field(default_factory=lambda: list[str]())
    names: list[Label] = field(default_factory=lambda: list[Label]())
    cardinals: list[Label] = field(default_factory=lambda: list[Label]())

    @property
    def font(self) -> float:
        return round(self.width / 70, 1)


@cache
def _catalogue() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DATA.read_text())
    return data


def constellations(*, latin: bool = False) -> dict[str, tuple[str, float, float]]:
    """All 88: IAU abbreviation → (name, RA, Dec of the label point, J2000). Names German,
    or with ``latin`` the IAU names."""
    data = _catalogue()
    return {
        key: (str(data["latin"][key]) if latin else str(name), float(ra), float(dec))
        for key, (name, ra, dec, _rank) in data["names"].items()
    }


def _julian(at: dt.datetime) -> float:
    return at.timestamp() / 86400.0 + 2440587.5


def sidereal_deg(at: dt.datetime, lon: float) -> float:
    """Local mean sidereal time in degrees (Meeus 12.4)."""
    jd = _julian(at)
    t = (jd - 2451545.0) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * t * t
        - t * t * t / 38710000.0
    )
    return (gmst + lon) % 360.0


def precess(ra: float, dec: float, at: dt.datetime) -> tuple[float, float]:
    """J2000 → date, low precision (Meeus 21.1): good to well under 0.1° for decades,
    except right next to the celestial pole."""
    years = (_julian(at) - 2451545.0) / 365.25
    m, n = 0.0128117 * years, 0.0055667 * years  # degrees
    d = max(-89.9, min(89.9, dec))
    ra2 = ra + m + n * math.sin(math.radians(ra)) * math.tan(math.radians(d))
    dec2 = dec + n * math.cos(math.radians(ra))
    return ra2 % 360.0, dec2


def alt_az(ra: float, dec: float, lst: float, lat: float) -> tuple[float, float]:
    """Altitude and azimuth (from north toward east), degrees."""
    h = math.radians((lst - ra) % 360.0)
    d, p = math.radians(dec), math.radians(lat)
    sin_alt = math.sin(d) * math.sin(p) + math.cos(d) * math.cos(p) * math.cos(h)
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))
    az = math.atan2(
        -math.cos(d) * math.sin(h),
        math.sin(d) * math.cos(p) - math.cos(d) * math.sin(p) * math.cos(h),
    )
    return math.degrees(alt), math.degrees(az) % 360.0


def tilt(alt: float, az: float, tilt_east: float, tilt_north: float) -> tuple[float, float]:
    """SPEC §4.8 step 1: the direction in the frame of a leaning camera axis."""
    tau = math.radians(math.hypot(tilt_east, tilt_north))
    if tau < 1e-14:
        return alt, az
    phi = math.atan2(tilt_east, tilt_north)
    a, z = math.radians(alt), math.radians(az)
    v = (math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a))
    axis = (math.sin(tau) * math.sin(phi), math.sin(tau) * math.cos(phi), math.cos(tau))
    k = (axis[1], -axis[0], 0.0)  # axis x (0, 0, 1)
    kn = math.hypot(k[0], k[1])
    k = (k[0] / kn, k[1] / kn, 0.0)
    kv = (k[1] * v[2] - k[2] * v[1], k[2] * v[0] - k[0] * v[2], k[0] * v[1] - k[1] * v[0])
    kdv = k[0] * v[0] + k[1] * v[1] + k[2] * v[2]
    c, s = math.cos(tau), math.sin(tau)
    w = [v[i] * c + kv[i] * s + k[i] * kdv * (1 - c) for i in range(3)]
    out_alt = math.degrees(math.asin(max(-1.0, min(1.0, w[2]))))
    return out_alt, math.degrees(math.atan2(w[0], w[1])) % 360.0


def project(
    alt: float, az: float, lens: Lens, north_deg: float, mirrored: bool
) -> tuple[float, float]:
    """SPEC §4.8: (alt, az) → pixel of the solved frame."""
    alt, az = tilt(alt, az, lens.tilt_east_deg, lens.tilt_north_deg)
    t = (90.0 - alt) / 90.0
    r = lens.a1 * t + lens.a3 * t**3
    ang = math.radians(north_deg - az if mirrored else north_deg + az)
    return lens.cx + r * math.sin(ang), lens.cy - r * math.cos(ang)


def usable(lens: Lens | None) -> bool:
    """A lens that can be drawn with (``width``/``height`` 0 = unknown, SPEC §6.3)."""
    return lens is not None and lens.width > 0 and lens.height > 0 and lens.a1 > 0


def overlay(
    lens: Lens, north_deg: float, mirrored: bool, lat: float, lon: float, at: dt.datetime
) -> Overlay:
    """Lines, names and N/O/S/W for an image taken ``at``, in pixels of the lens's
    ``width`` x ``height`` frame (scale for other sizes of the same frame)."""
    lst = sidereal_deg(at, lon)

    def here(ra: float, dec: float) -> tuple[float, float]:
        return alt_az(*precess(ra, dec, at), lst, lat)

    def pixel(alt: float, az: float) -> tuple[float, float]:
        return project(alt, az, lens, north_deg, mirrored)

    catalogue = _catalogue()
    paths: list[str] = []
    for strings in catalogue["lines"].values():
        for string in strings:
            points: list[tuple[float, float]] = []
            for (ra1, dec1), (ra2, dec2) in itertools.pairwise(string):
                dra = (ra2 - ra1 + 180.0) % 360.0 - 180.0
                steps = max(1, math.ceil(math.hypot(dra, dec2 - dec1) / STEP_DEG))
                points += [
                    here(ra1 + dra * i / steps, dec1 + (dec2 - dec1) * i / steps)
                    for i in range(steps)
                ]
            points.append(here(*string[-1]))
            piece: list[str] = []
            for alt, az in points:
                if alt < 0:
                    if len(piece) > 1:
                        paths.append("M" + "L".join(piece))
                    piece = []
                    continue
                x, y = pixel(alt, az)
                piece.append(f"{x:.1f} {y:.1f}")
            if len(piece) > 1:
                paths.append("M" + "L".join(piece))
    names: list[Label] = []
    for name, ra, dec, rank in catalogue["names"].values():
        alt, az = here(ra, dec)
        if alt >= LABEL_MIN_ALT and rank <= LABEL_MAX_RANK:
            x, y = pixel(alt, az)
            names.append(Label(x, y, name))
    result = Overlay(lens.width, lens.height, paths, names)
    margin = result.font
    for text, az in CARDINALS:
        x, y = _into_frame(*pixel(CARDINAL_ALT, az), lens, margin)
        result.cardinals.append(Label(x, y, text))
    return result


def _into_frame(x: float, y: float, lens: Lens, margin: float) -> tuple[float, float]:
    """A point outside the frame (a 16:9 crop cuts off the horizon at top and bottom)
    moved toward the image centre until it lies ``margin`` inside the edge, so it still
    shows its true direction."""
    lo_x, hi_x = margin, lens.width - margin
    lo_y, hi_y = margin, lens.height - margin
    if lo_x <= x <= hi_x and lo_y <= y <= hi_y:
        return x, y
    cx = min(max(lens.cx, lo_x), hi_x)
    cy = min(max(lens.cy, lo_y), hi_y)
    t = 1.0
    for d, c, lo, hi in ((x - cx, cx, lo_x, hi_x), (y - cy, cy, lo_y, hi_y)):
        if d > 0:
            t = min(t, (hi - c) / d)
        elif d < 0:
            t = min(t, (lo - c) / d)
    return cx + (x - cx) * t, cy + (y - cy) * t


@dataclass(frozen=True)
class SkyLines:
    """Constellations as directions (altitude, azimuth in degrees) for a place and time,
    for views that project them themselves (the app's sky view, roadmap N6)."""

    lines: list[list[tuple[float, float]]]
    names: list[tuple[str, float, float]]


def sky_lines(
    lat: float, lon: float, at: dt.datetime, min_alt: float = -5.0, *, latin: bool = False
) -> SkyLines:
    """Lines cut below ``min_alt``, split every ``STEP_DEG`` like in ``overlay``. Names are
    German, or with ``latin`` the IAU names English uses (Auriga for Fuhrmann)."""
    lst = sidereal_deg(at, lon)

    def here(ra: float, dec: float) -> tuple[float, float]:
        alt, az = alt_az(*precess(ra, dec, at), lst, lat)
        return round(alt, 2), round(az, 2)

    catalogue = _catalogue()
    lines: list[list[tuple[float, float]]] = []
    for strings in catalogue["lines"].values():
        for string in strings:
            points: list[tuple[float, float]] = []
            for (ra1, dec1), (ra2, dec2) in itertools.pairwise(string):
                dra = (ra2 - ra1 + 180.0) % 360.0 - 180.0
                steps = max(1, math.ceil(math.hypot(dra, dec2 - dec1) / STEP_DEG))
                points += [
                    here(ra1 + dra * i / steps, dec1 + (dec2 - dec1) * i / steps)
                    for i in range(steps)
                ]
            points.append(here(*string[-1]))
            piece: list[tuple[float, float]] = []
            for point in points:
                if point[0] < min_alt:
                    if len(piece) > 1:
                        lines.append(piece)
                    piece = []
                    continue
                piece.append(point)
            if len(piece) > 1:
                lines.append(piece)
    names = [
        (catalogue["latin"][key] if latin else name, *here(ra, dec))
        for key, (name, ra, dec, rank) in catalogue["names"].items()
        if rank <= LABEL_MAX_RANK
    ]
    return SkyLines(lines, [n for n in names if n[1] >= min_alt])
