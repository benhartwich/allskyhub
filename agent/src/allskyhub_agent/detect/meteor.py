"""Meteor detection (roadmap #1), ported from Benjamin's allsky_meteordetect module. The
part without memory (streaks in a difference, the checks of one frame) is allskyhub_detect,
shared with the hub; here a streak is followed from frame to frame, with the sky checks.

Temporal approach: the difference to the previous frame keeps only new light, so stars and
static clouds cancel out. Long, thin streaks in it are candidates. Whether a candidate is a
meteor is decided one frame later: a satellite or aircraft goes on in the next frame (or
came from the previous one), a meteor is in one frame only. Guards against the false
alarms the module met on real nights: twinkling stars and registration jitter (minimum
length and elongation), recurring spots (hot pixels, a bright star), and frames with many
candidates at once (moving clouds, wind).
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import numpy.typing as npt

from allskyhub_agent.core.metering import circle_mask
from allskyhub_agent.detect import trail
from allskyhub_detect.meteor import Gray as Gray
from allskyhub_detect.meteor import Mask as Mask
from allskyhub_detect.meteor import MeteorConfig as MeteorConfig
from allskyhub_detect.meteor import Streak as Streak
from allskyhub_detect.meteor import angle_diff as _angle_diff
from allskyhub_detect.meteor import axis_samples as axis_samples
from allskyhub_detect.meteor import centre_line as centre_line
from allskyhub_detect.meteor import dash_runs as dash_runs
from allskyhub_detect.meteor import difference_streaks
from allskyhub_detect.meteor import find_streaks as find_streaks
from allskyhub_detect.meteor import new_light_fraction as new_light_fraction
from allskyhub_detect.meteor import progressing as progressing
from allskyhub_detect.meteor import similar as similar
from allskyhub_detect.meteor import to_gray as to_gray
from allskyhub_protocol import FrameInfo, Mode
from allskyhub_solve.solve import Solution, alt_az, sidereal_deg

# Major annual showers: name, start (month, day), end (month, day), peak ZHR. Without an
# orientation the date is all there is; with one the radiant decides (`match_shower`).
SHOWERS: tuple[tuple[str, tuple[int, int], tuple[int, int], int], ...] = (
    ("Quadrantids", (12, 28), (1, 12), 110),
    ("Lyrids", (4, 16), (4, 25), 18),
    ("Eta Aquariids", (4, 19), (5, 28), 50),
    ("Delta Aquariids", (7, 12), (8, 23), 25),
    ("Perseids", (7, 17), (8, 24), 100),
    ("Orionids", (10, 2), (11, 7), 20),
    ("Leonids", (11, 6), (11, 30), 15),
    ("Geminids", (12, 4), (12, 17), 150),
    ("Ursids", (12, 17), (12, 26), 10),
)


# Radiant at the peak (IMO working list): peak (month, day), RA, Dec, and its daily drift
# in RA and Dec (degrees per day).
RADIANTS: dict[str, tuple[tuple[int, int], float, float, float, float]] = {
    "Quadrantids": ((1, 4), 230.0, 49.0, 0.8, -0.2),
    "Lyrids": ((4, 22), 271.0, 34.0, 1.1, 0.0),
    "Eta Aquariids": ((5, 6), 338.0, -1.0, 0.9, 0.4),
    "Delta Aquariids": ((7, 30), 340.0, -16.0, 0.8, 0.18),
    "Perseids": ((8, 12), 48.0, 58.0, 1.35, 0.12),
    "Orionids": ((10, 21), 95.0, 16.0, 0.7, 0.1),
    "Leonids": ((11, 17), 152.0, 22.0, 0.7, -0.4),
    "Geminids": ((12, 14), 112.0, 33.0, 1.0, -0.15),
    "Ursids": ((12, 22), 217.0, 76.0, 0.0, 0.0),
}
RADIANT_TOL_DEG = 6.0  # the backward path passes the radiant this close
RADIANT_MIN_ALT = -5.0  # a radiant just below the horizon still sends earth-grazers


def _active(day: date) -> list[tuple[int, str]]:
    v = day.month * 100 + day.day
    out: list[tuple[int, str]] = []
    for name, (m1, d1), (m2, d2), zhr in SHOWERS:
        a, b = m1 * 100 + d1, m2 * 100 + d2
        if (a <= v <= b) if a <= b else (v >= a or v <= b):
            out.append((zhr, name))
    return out


def active_shower(day: date) -> str | None:
    """The strongest shower active on `day`, if any."""
    active = _active(day)
    return max(active)[1] if active else None


def radiant(name: str, day: date) -> tuple[float, float]:
    """(RA, Dec) of a shower's radiant on `day`, drifting from its peak position."""
    (pm, pd), ra, dec, dra, ddec = RADIANTS[name]
    days = min(
        ((day - date(y, pm, pd)).days for y in (day.year - 1, day.year, day.year + 1)), key=abs
    )
    return (ra + dra * days) % 360.0, dec + ddec * days


def sky_unit(az: float, alt: float) -> npt.NDArray[np.float64]:
    a, e = math.radians(az), math.radians(alt)
    return np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)])


def angle_between(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), float(a @ b)))


def radiant_distance(
    end1: tuple[float, float], end2: tuple[float, float], rad: tuple[float, float]
) -> float | None:
    """How far (degrees) a radiant at (az, alt) is from the meteor's path between the ends
    (az, alt) extended along its great circle; None if the radiant lies on the trail
    itself: a meteor moves away from its radiant and never crosses it."""
    p1, p2, r = sky_unit(*end1), sky_unit(*end2), sky_unit(*rad)
    n = np.cross(p1, p2)
    norm = float(np.linalg.norm(n))
    if norm < 1e-6:
        return None
    n = n / norm
    off = math.degrees(math.asin(min(1.0, abs(float(n @ r)))))
    q = r - float(n @ r) * n
    qn = float(np.linalg.norm(q))
    if qn < 1e-9:
        return off  # the radiant is the circle's pole: 90 degrees off anyway
    q = q / qn
    if angle_between(p1, q) + angle_between(q, p2) - angle_between(p1, p2) < 1e-3:
        return None
    return off


def match_shower(
    end1: tuple[float, float],
    end2: tuple[float, float],
    utc: datetime,
    lat: float,
    lon: float,
) -> str | None:
    """The active shower whose radiant the meteor's path points back to, None: sporadic.
    `end1`, `end2` are the trail's ends as (azimuth, altitude)."""
    lst = sidereal_deg(utc, lon)
    best: tuple[float, str] | None = None
    for _, name in _active(utc.date()):
        ra, dec = radiant(name, utc.date())
        alt, az = alt_az(np.array([ra]), np.array([dec]), lst, lat)
        if float(alt[0]) < RADIANT_MIN_ALT:
            continue
        d = radiant_distance(end1, end2, (float(az[0]), float(alt[0])))
        if d is not None and d <= RADIANT_TOL_DEG and (best is None or d < best[0]):
            best = (d, name)
    return best[1] if best else None


@dataclass(frozen=True)
class MeteorHit:
    """A streak decided one frame later: the frame it is in, the streak and the verdict
    from its trail (SPEC §6.4 Trail verdicts), `meteor` when nothing speaks against one."""

    frame: FrameInfo
    image_path: Path
    streak: Streak
    trail: str = "meteor"


@dataclass
class _Pending:
    frame: FrameInfo
    image_path: Path
    streaks: list[Streak]
    dashes: list[int]
    gray: Gray  # the frame the streaks are in
    before: Gray  # and the one before it
    moving: set[int]  # streaks that continue one of the frame before
    diff: Gray  # the difference the streaks are in
    prior: list[tuple[Streak, FrameInfo]]  # the streaks of the frame before, for the circle


class MeteorDetector:
    """Feed every stored night frame; returns the meteors confirmed for the frame before.

    A meteor is in exactly one frame, so it shows up in two consecutive difference images
    at the same place: when it appears and when it disappears. A candidate is confirmed
    only then, and only if nothing continues it (satellite, aircraft), it is not at a spot
    that keeps changing (star, bloom), and its trail is not dashed (strobing aircraft).
    """

    def __init__(
        self,
        cfg: MeteorConfig | None = None,
        mask: Mask | None = None,
        mask_radius_frac: float | None = None,
        orientation: Callable[[], Solution | None] | None = None,
        location: Callable[[], tuple[float, float] | None] | None = None,
    ) -> None:
        self._cfg = cfg or MeteorConfig()
        # SPEC §4.8: the sky checks (great circle, star trail, star) need the lens.
        self._orientation = orientation or (lambda: None)
        self._location = location or (lambda: None)
        self._stars = trail.StarField()
        self._edge: npt.NDArray[np.float32] | None = None
        self._edge_of: Mask | None = None
        self._mask = mask
        # Without a mask: the image circle of the hardware profile, built for the first frame.
        self._mask_frac = mask_radius_frac
        self._prev_gray: Gray | None = None
        self._pending: _Pending | None = None
        self._hotspots: deque[tuple[float, float, float]] = deque(maxlen=2000)

    @property
    def config(self) -> MeteorConfig:
        return self._cfg

    def set_mask(self, mask: Mask) -> None:
        """Use the learned sky mask (SPEC §4.7) instead of the image circle."""
        self._mask = mask

    def reset(self) -> None:
        """Forget the previous frame (mode change, camera restart)."""
        self._prev_gray = None
        self._pending = None

    def _streaks(self, gray: Gray, prev: Gray) -> tuple[list[Streak], Gray] | None:
        """Candidates in the difference of two frames; None for a cloudy or twinkling frame."""
        return difference_streaks(gray, prev, self._cfg, self._mask)

    def _edge_distance(self) -> npt.NDArray[np.float32] | None:
        if self._mask is None:
            return None
        if self._edge is None or self._edge_of is not self._mask:
            self._edge, self._edge_of = trail.edge_distance(self._mask), self._mask
        return self._edge

    def _verdict(
        self, cand: Streak, dashes: int, pending: _Pending, frame: FrameInfo, after: list[Streak]
    ) -> str:
        """Why the streak is not a meteor, in the module's order, or `meteor`."""
        cfg = self._cfg
        sol, loc = self._orientation(), self._location()
        shape = pending.gray.shape
        if sol is not None:
            pf = pending.frame
            others = [(o, frame) for o in after if not similar(o, cand, cfg)] + pending.prior
            for o, of in others:
                times = (pf.captured_at, pf.exposure_us / 1e6, of.captured_at, of.exposure_us / 1e6)
                if trail.same_circle(cand, o, sol, shape, times):
                    return "circle"
        recent = [
            h for h in self._hotspots if frame.captured_at.timestamp() - h[2] <= cfg.repeat_window_s
        ]
        if (
            sum(
                1
                for hx, hy, _ in recent
                if math.hypot(hx - cand.cx, hy - cand.cy) <= cfg.repeat_radius_px
            )
            >= cfg.repeat_k
        ):
            return "repeat"
        short = cand.length <= cfg.short_max_px
        if sol is not None and loc is not None and short:
            ta = trail.star_trail_angle(sol, shape, cand.cx, cand.cy, loc[0])
            if ta is not None and _angle_diff(cand.angle_deg, ta) <= cfg.star_trail_tol_deg:
                return "star_trail"
            mid = pending.frame.captured_at
            if self._stars.near(
                sol, shape, mid, loc[0], loc[1], cand.cx, cand.cy, cfg.star_radius_px
            ):
                return "star"
        if trail.glare_spike(
            cand, trail.glare_sources(pending.gray), cfg.glare_radii, cfg.glare_tol_deg
        ):
            return "glare"
        if cand.length >= cfg.dash_min_len_px and dashes >= cfg.dash_runs:
            return "dashed"
        if (
            cand.length >= cfg.frag_min_len_px
            and trail.collinear_fragments(pending.diff, cand, cfg.diff_threshold) >= cfg.frag_min
        ):
            return "fragmented"
        edge = self._edge_distance()
        if (
            edge is not None
            and edge.shape == shape
            and cand.length >= cfg.edge_min_len_px
            and cand.elongation < cfg.edge_max_elongation
            and trail.edge_hug(cand, edge) < cfg.edge_margin_px
        ):
            return "edge_glow"
        return "meteor"

    def feed(self, frame: FrameInfo, gray: Gray, image_path: Path) -> list[MeteorHit]:
        cfg = self._cfg
        if frame.mode is not Mode.NIGHT:
            self.reset()
            return []
        if self._mask is not None and self._mask.shape != gray.shape:
            self._mask = None  # a different resolution: detect on the whole frame
        if self._mask is None and self._mask_frac is not None:
            self._mask = circle_mask(gray.shape[0], gray.shape[1], self._mask_frac)
        prev, self._prev_gray = self._prev_gray, gray
        if prev is None or prev.shape != gray.shape:
            self._pending = None
            return []
        found = self._streaks(gray, prev)
        if found is None:  # cloudy or twinkling: nothing from here is trusted
            self._pending = None
            return []
        streaks, diff = found

        hits: list[MeteorHit] = []
        pending = self._pending
        if pending is not None:
            for i, (cand, dashes) in enumerate(zip(pending.streaks, pending.dashes, strict=True)):
                if i in pending.moving or any(progressing(cur, cand, cfg) for cur in streaks):
                    # It goes on: satellite or aircraft (SPEC §6.4 `moving`).
                    hits.append(MeteorHit(pending.frame, pending.image_path, cand, "moving"))
                    continue
                if not any(similar(cur, cand, cfg) for cur in streaks):
                    continue  # no disappearance at the same place: flicker, no event
                light = new_light_fraction(
                    cand, pending.before, pending.gray, gray, cfg.diff_threshold
                )
                if light < cfg.min_new_light:
                    continue  # also there before or after: a drifting star, no event
                verdict = self._verdict(cand, dashes, pending, frame, streaks)
                hits.append(MeteorHit(pending.frame, pending.image_path, cand, verdict))
            for cand in pending.streaks:
                self._hotspots.append((cand.cx, cand.cy, pending.frame.captured_at.timestamp()))

        dashes = [dash_runs(gray, st) if st.length >= cfg.dash_min_len_px else 0 for st in streaks]
        before = pending.streaks if pending is not None else []
        moving = {i for i, st in enumerate(streaks) if any(progressing(b, st, cfg) for b in before)}
        prior = [(b, pending.frame) for b in before] if pending is not None else []
        self._pending = (
            _Pending(frame, image_path, streaks, dashes, gray, prev, moving, diff, prior)
            if streaks
            else None
        )
        return hits

    def flush(self) -> list[MeteorHit]:
        """End of the night: the last candidates can't be confirmed (no disappearance)."""
        self._pending = None
        return []
