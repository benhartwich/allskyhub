"""Trail verdicts: why a streak is not a meteor (SPEC §6.4 Trail verdicts, roadmap L25).

Ported from the checks of Benjamin's Allsky module Meteor Detection, with its thresholds.
The checks that need the sky (great circle, star trail, star) use the plate-solved lens
(SPEC §4.8) and are skipped while the camera has no orientation.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_agent.calib.solve import Solution, alt_az, sidereal_deg
from allskyhub_protocol import Event, EventKind, EventLabel, LabelReason

if TYPE_CHECKING:
    from allskyhub_agent.detect.meteor import Streak

F = npt.NDArray[np.float64]
Gray = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]

STARS = Path(__file__).parent.parent / "calib" / "stars_mag5.json"

# SPEC §6.4: the automatic label of each verdict.
LABELS: dict[str, tuple[LabelReason, str]] = {
    "moving": ("other", "bewegtes Objekt"),
    "circle": ("other", "bewegtes Objekt"),
    "dashed": ("aircraft", "blinkt"),
    "fragmented": ("other", "Bruchstücke"),
    "star": ("artifact", "Stern"),
    "star_trail": ("artifact", "Sternspur"),
    "glare": ("artifact", "Blendung"),
    "edge_glow": ("artifact", "Randleuchten"),
    "repeat": ("artifact", "wiederkehrend"),
}

# Great circle (the module's CIRCLE_*): every end and centre of both streaks within 1.2° of
# one circle, the other streak at most 120° along it, both at least 6° long, or at least 2°
# when the gap fits the object's speed.
CIRCLE_TOL_DEG = 1.2
CIRCLE_MAX_GAP_DEG = 120.0
CIRCLE_MIN_LEN_DEG = 6.0
CIRCLE_SHORT_MIN_DEG = 2.0
CIRCLE_SPEED_RANGE = (0.5, 2.0)
SIDEREAL_DEG_PER_S = 15.041 / 3600.0
GLARE_LEVEL = 250
GLARE_MIN_AREA_12MP = 2000.0
GLARE_MAX_R_12MP = 300.0


def label_for(event: Event, now: datetime | None = None) -> EventLabel | None:
    """The automatic label of a meteor event the camera judged not to be one."""
    trail = event.data.get("trail")
    if event.kind is not EventKind.METEOR or not isinstance(trail, str) or trail not in LABELS:
        return None
    reason, detail = LABELS[trail]
    return EventLabel(
        night_id=event.night_id,
        event_id=event.id,
        label="false_positive",
        reason=reason,
        auto=True,
        detail=detail,
        at=now or datetime.now(UTC),
    )


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _unit(alt: float, az: float) -> F:
    a, e = math.radians(az), math.radians(alt)
    return np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)])


def _fits(sol: Solution, shape: tuple[int, ...]) -> bool:
    """The lens was solved on frames of this size (or of an unknown size)."""
    return not sol.width or (shape[1], shape[0]) == (sol.width, sol.height)


def sky_vector(sol: Solution, shape: tuple[int, ...], x: float, y: float) -> F | None:
    if not _fits(sol, shape):
        return None
    alt, az = sol.unproject(np.array([x]), np.array([y]))
    return _unit(float(alt[0]), float(az[0]))


def _angle(u: F, v: F) -> float:
    c = float(u @ v) / (float(np.linalg.norm(u)) * float(np.linalg.norm(v)) + 1e-12)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def same_circle(
    a: Streak,
    b: Streak,
    sol: Solution,
    shape: tuple[int, ...],
    times: tuple[datetime, float, datetime, float] | None = None,
) -> bool:
    """a and b lie on one great circle, b beyond a: a satellite or aircraft in two frames.
    `times`: exposure start and length (s) of a's and b's frames, for short pieces."""
    pts: list[F] = []
    for st in (a, b):
        for x, y in (st.p1, st.p2, (st.cx, st.cy)):
            v = sky_vector(sol, shape, x, y)
            if v is None:
                return False
            pts.append(v)
    m = np.array(pts)
    normal = np.linalg.svd(m)[2][-1]
    if float(np.max(np.abs(m @ normal))) > math.sin(math.radians(CIRCLE_TOL_DEG)):
        return False
    la, lb = _angle(m[0], m[1]), _angle(m[3], m[4])
    gap = _angle(m[2], m[5])
    if not (la + lb) / 2.0 < gap <= CIRCLE_MAX_GAP_DEG:
        return False
    if min(la, lb) >= CIRCLE_MIN_LEN_DEG:
        return True
    if min(la, lb) < CIRCLE_SHORT_MIN_DEG or times is None:
        return False
    ta, ea, tb, eb = times
    if min(ea, eb) <= 0:
        return False
    dt = abs((tb.timestamp() + eb / 2.0) - (ta.timestamp() + ea / 2.0))
    speed = max(la / ea, lb / eb)
    if dt <= 0 or speed <= 0:
        return False
    lo, hi = CIRCLE_SPEED_RANGE
    return lo <= gap / (speed * dt) <= hi


def star_trail_angle(
    sol: Solution, shape: tuple[int, ...], x: float, y: float, lat: float
) -> float | None:
    """The image direction (0..180, from the x axis) the stars move in at a pixel: rotate
    its sky vector about the celestial pole by 60 s and see where it lands."""
    if not _fits(sol, shape):
        return None
    alt, az = sol.unproject(np.array([x]), np.array([y]))
    a0, z0 = float(alt[0]), float(az[0])
    if a0 <= 0.5:
        return None
    v = _unit(a0, z0)
    pole = _unit(lat, 0.0)
    th = math.radians(SIDEREAL_DEG_PER_S * 60.0)
    v2 = (
        v * math.cos(th)
        + np.cross(pole, v) * math.sin(th)
        + pole * float(pole @ v) * (1 - math.cos(th))
    )
    alt2 = math.degrees(math.asin(max(-1.0, min(1.0, float(v2[2])))))
    az2 = math.degrees(math.atan2(float(v2[0]), float(v2[1]))) % 360.0
    px, py = sol.project(np.array([a0, alt2]), np.array([z0, az2]))
    return math.degrees(math.atan2(float(py[1] - py[0]), float(px[1] - px[0]))) % 180.0


@cache
def _catalogue() -> tuple[F, F, F]:
    rows: list[list[float]] = json.loads(STARS.read_text(encoding="utf-8"))["stars"]
    return (
        np.array([r[0] for r in rows]),
        np.array([r[1] for r in rows]),
        np.array([r[2] for r in rows]),
    )


class StarField:
    """Catalogue stars to magnitude 5 above 12° in pixels, cached per minute."""

    def __init__(self, max_mag: float = 5.0, min_alt: float = 12.0) -> None:
        self._max_mag = max_mag
        self._min_alt = min_alt
        self._key: tuple[object, ...] | None = None
        self._xy: F = np.empty((0, 2))

    def pixels(
        self, sol: Solution, shape: tuple[int, ...], utc: datetime, lat: float, lon: float
    ) -> F:
        if not _fits(sol, shape):
            return np.empty((0, 2))
        key = (id(sol), utc.replace(second=0, microsecond=0), lat, lon)
        if key != self._key:
            ra, dec, mag = _catalogue()
            keep = mag <= self._max_mag
            alt, az = alt_az(ra[keep], dec[keep], sidereal_deg(utc, lon), lat)
            up = alt >= self._min_alt
            px, py = sol.project(alt[up], az[up])
            self._xy = np.column_stack((px, py)) if px.size else np.empty((0, 2))
            self._key = key
        return self._xy

    def near(
        self,
        sol: Solution,
        shape: tuple[int, ...],
        utc: datetime,
        lat: float,
        lon: float,
        x: float,
        y: float,
        radius: float,
    ) -> bool:
        xy = self.pixels(sol, shape, utc, lat, lon)
        if xy.size == 0:
            return False
        return float(np.min(np.hypot(xy[:, 0] - x, xy[:, 1] - y))) <= radius


def glare_sources(gray: Gray) -> list[tuple[float, float, float]]:
    """Saturated discs big enough to throw spikes (moon, lamps): (x, y, radius)."""
    h, w = gray.shape[:2]
    scale = (w * h) / 12.3e6
    min_area = GLARE_MIN_AREA_12MP * scale
    max_r = GLARE_MAX_R_12MP * math.sqrt(scale)
    sat = (gray >= GLARE_LEVEL).astype(np.uint8)
    n, _, stats, cent = cv2.connectedComponentsWithStats(sat)
    out: list[tuple[float, float, float]] = []
    for i in range(1, int(n)):
        area = float(stats[i, cv2.CC_STAT_AREA])
        r = math.sqrt(area / math.pi)
        if area >= min_area and r <= max_r:
            out.append((float(cent[i][0]), float(cent[i][1]), r))
    return out


def glare_spike(
    s: Streak, sources: list[tuple[float, float, float]], radii: float, tol_deg: float
) -> bool:
    """The streak points straight away from a bright light within `radii` of its radius."""
    for x, y, r in sources:
        d = math.hypot(s.cx - x, s.cy - y)
        if d <= r or d > radii * r:
            continue
        radial = math.degrees(math.atan2(s.cy - y, s.cx - x)) % 180.0
        if _angle_diff(s.angle_deg, radial) <= tol_deg:
            return True
    return False


def collinear_fragments(
    diff: Gray, s: Streak, threshold: int, perp_tol: float = 8.0, reach: float = 3.0
) -> int:
    """Pieces of the difference image in line with the streak beyond its ends (up to
    `reach` half-lengths): the broken tail of a satellite or aircraft glint."""
    a = math.radians(s.angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    half = s.length / 2.0
    _, bw = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    n, _, stats, cent = cv2.connectedComponentsWithStats(bw, connectivity=8)
    count = 0
    for i in range(1, int(n)):
        if int(stats[i, cv2.CC_STAT_AREA]) < 6:
            continue
        px, py = float(cent[i][0]) - s.cx, float(cent[i][1]) - s.cy
        axial = px * dx + py * dy
        perp = abs(-px * dy + py * dx)
        if perp <= perp_tol and half < abs(axial) <= half * reach:
            count += 1
    return count


def edge_distance(mask: Mask) -> npt.NDArray[np.float32]:
    """Distance of every pixel to the nearest pixel outside the sky mask."""
    out: npt.NDArray[np.float32] = np.asarray(
        cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5), dtype=np.float32
    )
    return out


def edge_hug(s: Streak, dist: npt.NDArray[np.float32]) -> float:
    """How far the streak's farther end is from the mask's edge (px)."""
    h, w = dist.shape
    return max(
        float(dist[min(max(int(y), 0), h - 1), min(max(int(x), 0), w - 1)]) for x, y in (s.p1, s.p2)
    )
