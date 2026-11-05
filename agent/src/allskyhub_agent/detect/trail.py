"""Trail verdicts: why a streak is not a meteor (SPEC §6.4 Trail verdicts, roadmap L25).

Ported from the checks of Benjamin's Allsky module Meteor Detection, with its thresholds.
The checks that need the sky (great circle, star trail, star) use the plate-solved lens
(SPEC §4.8) and are skipped while the camera has no orientation; those of a single frame
(glare, fragments, edge glow) are shared with the hub in allskyhub_detect.trail.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from functools import cache
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from allskyhub_detect.trail import collinear_fragments as collinear_fragments
from allskyhub_detect.trail import edge_distance as edge_distance
from allskyhub_detect.trail import edge_hug as edge_hug
from allskyhub_detect.trail import glare_sources as glare_sources
from allskyhub_detect.trail import glare_spike as glare_spike
from allskyhub_protocol import Event, EventKind, EventLabel, LabelReason
from allskyhub_solve.solve import STARS_MAG5, Solution, alt_az, sidereal_deg

if TYPE_CHECKING:
    from allskyhub_detect.meteor import Streak

F = npt.NDArray[np.float64]

STARS = STARS_MAG5

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
