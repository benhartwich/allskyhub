"""The trail checks of a single frame (SPEC §6.4 Trail verdicts): glare spikes from a
bright light, collinear fragments of a satellite's or aircraft's glint, and a glow along
the sky mask's edge. Ported from Benjamin's Allsky module Meteor Detection, with its
thresholds. The checks that need the sky (great circle, star trail, star) stay with the
camera agent, which has the plate-solved lens (SPEC §4.8).
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import cv2
import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from allskyhub_detect.meteor import Streak

Gray = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]

GLARE_LEVEL = 250
GLARE_MIN_AREA_12MP = 2000.0
GLARE_MAX_R_12MP = 300.0


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


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
