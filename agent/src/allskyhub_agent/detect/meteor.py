"""Meteor detection (roadmap #1), ported from Benjamin's allsky_meteordetect module.

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
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_agent.core.metering import circle_mask
from allskyhub_protocol import FrameInfo, Mode

Gray = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]

# Major annual showers: name, start (month, day), end (month, day), peak ZHR. Date-based
# context only; geometric radiant matching needs a calibrated fisheye (roadmap #8).
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


def active_shower(day: date) -> str | None:
    """The strongest shower active on `day`, if any."""
    v = day.month * 100 + day.day
    best: tuple[int, str] | None = None
    for name, (m1, d1), (m2, d2), zhr in SHOWERS:
        a, b = m1 * 100 + d1, m2 * 100 + d2
        if ((a <= v <= b) if a <= b else (v >= a or v <= b)) and (best is None or zhr > best[0]):
            best = (zhr, name)
    return best[1] if best else None


@dataclass(frozen=True)
class MeteorConfig:
    """Defaults of the allsky_meteordetect module, tuned on real nights."""

    diff_threshold: int = 22  # change above this (8 bit) counts
    min_length_px: float = 50.0  # 4 sigma along the axis; shorter: twinkling stars
    min_elongation: float = 5.0
    min_area_px: int = 12
    max_area_px: int = 6000
    cloud_fraction: float = 0.02  # more of the sky changed than this: cloudy, skip the frame
    scint_max: int = 8  # more candidates: twinkle; only a clearly dominant one is kept
    scint_dominance: float = 1.6
    similar_px: float = 60.0  # the disappearance must be this close ...
    similar_angle_deg: float = 20.0  # ... and this parallel
    progress_min_px: float = 25.0  # a continuing object moved at least this far ...
    progress_max_px: float = 400.0  # ... and at most this far between two frames
    progress_angle_deg: float = 25.0
    repeat_radius_px: float = 55.0  # this many earlier streaks within this radius ...
    repeat_k: int = 3
    repeat_window_s: float = 1500.0  # ... in this time: a hot spot (star, bloom), not a meteor
    dash_min_len_px: float = 120.0  # long streaks with this many bright segments ...
    dash_runs: int = 10  # ... are a blinking aircraft or a tumbling satellite
    # Share of the trail that is brighter only in its own frame (not before, not after).
    # A meteor is in one frame only; a slowly drifting bright star near the horizon is in
    # all of them and makes short "streaks" at the same place frame after frame.
    min_new_light: float = 0.5


@dataclass(frozen=True)
class Streak:
    cx: float
    cy: float
    length: float
    elongation: float
    angle_deg: float  # axis angle from the image x axis, 0..180
    p1: tuple[float, float]
    p2: tuple[float, float]
    area: int
    peak: float  # 0..1, brightest new light on the streak
    # Offset across the axis as a quadratic in the distance along it (from the centre):
    # the fisheye bends a long streak near the edge by 10 px and more.
    bend: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def direction_deg(self) -> float:
        """Axis direction in the image, 0 = up, clockwise, 0..180 (a streak has no sign)."""
        dx = math.cos(math.radians(self.angle_deg))
        dy = math.sin(math.radians(self.angle_deg))
        return round(math.degrees(math.atan2(dx, -dy)) % 180.0, 1)


def to_gray(image: npt.NDArray[np.uint8]) -> Gray:
    """Luminance with single-pixel noise smoothed away (as the module does before detecting)."""
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    out: Gray = np.asarray(cv2.GaussianBlur(gray, (3, 3), 0), dtype=np.uint8)
    return out


def find_streaks(diff: Gray, cfg: MeteorConfig) -> list[Streak]:
    """Long, thin bright components of a difference image."""
    _, bw = cv2.threshold(diff, cfg.diff_threshold, 255, cv2.THRESH_BINARY)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    out: list[Streak] = []
    for i in range(1, int(n)):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < cfg.min_area_px or area > cfg.max_area_px:
            continue
        x0, y0 = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        w, h = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        # Only the bounding box: scanning the whole label image per component is far too slow.
        ys, xs = np.nonzero(labels[y0 : y0 + h, x0 : x0 + w] == i)
        if len(xs) < 5:
            continue
        ys = ys + y0
        xs = xs + x0
        pts = np.column_stack((xs, ys)).astype(np.float32)
        mean, evec, evals = cv2.PCACompute2(pts, np.empty((0,), dtype=np.float32))
        major = 4.0 * math.sqrt(max(float(evals[0, 0]), 1e-6))
        minor = 4.0 * math.sqrt(max(float(evals[1, 0]), 1e-6))
        if major < cfg.min_length_px:
            continue
        elong = major / (minor + 1e-6)
        if elong < cfg.min_elongation:
            continue
        cx, cy = float(mean[0, 0]), float(mean[0, 1])
        dx, dy = float(evec[0][0]), float(evec[0][1])
        # The thresholds use 4 sigma (as the module was tuned); length and ends are the real
        # extent along the axis.
        u = (xs - cx) * dx + (ys - cy) * dy
        u0, u1 = float(u.min()), float(u.max())
        v = (xs - cx) * -dy + (ys - cy) * dx
        bend = (0.0, 0.0, 0.0)
        if len(xs) >= 20:
            c2, c1, c0 = np.polyfit(u, v, 2)
            bend = (float(c2), float(c1), float(c0))
        out.append(
            Streak(
                cx=cx,
                cy=cy,
                length=u1 - u0,
                elongation=elong,
                angle_deg=math.degrees(math.atan2(dy, dx)) % 180.0,
                p1=(cx + dx * u0, cy + dy * u0),
                p2=(cx + dx * u1, cy + dy * u1),
                area=area,
                peak=float(diff[ys, xs].max()) / 255.0,
                bend=bend,
            )
        )
    return out


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def progressing(a: Streak, b: Streak, cfg: MeteorConfig) -> bool:
    """b continues a: a satellite or aircraft seen in two frames."""
    dc = math.hypot(a.cx - b.cx, a.cy - b.cy)
    return (
        cfg.progress_min_px < dc < cfg.progress_max_px
        and _angle_diff(a.angle_deg, b.angle_deg) < cfg.progress_angle_deg
    )


def similar(a: Streak, b: Streak, cfg: MeteorConfig) -> bool:
    """b is a at the same place: a meteor's appearance and its disappearance."""
    return (
        math.hypot(a.cx - b.cx, a.cy - b.cy) < cfg.similar_px
        and _angle_diff(a.angle_deg, b.angle_deg) < cfg.similar_angle_deg
    )


def dash_runs(gray: Gray, s: Streak) -> int:
    """Separate bright segments along the streak in the frame itself: a meteor is one
    continuous trail (1-2 runs), a strobing aircraft or tumbling satellite is dashed."""
    length = s.length
    if length < 1.0:
        return 0
    n = max(8, int(length))
    dx = math.cos(math.radians(s.angle_deg))
    dy = math.sin(math.radians(s.angle_deg))
    # Positions along the axis relative to the centre, following the streak's bend.
    u = np.linspace(-length / 2.0, length / 2.0, n + 1)
    c2, c1, c0 = s.bend
    off = c2 * u * u + c1 * u + c0
    h, w = gray.shape
    vals = np.zeros(n + 1, dtype=np.float32)
    for o in range(-3, 4):  # a small window across the axis, robust to a slight misfit
        xs = np.rint(s.cx + dx * u - dy * (off + o)).astype(np.int64)
        ys = np.rint(s.cy + dy * u + dx * (off + o)).astype(np.int64)
        ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        line = np.zeros(n + 1, dtype=np.float32)
        line[ok] = gray[ys[ok], xs[ok]]
        vals = np.maximum(vals, line)
    # Smooth over 5 px and ignore gaps and segments of a few px: sensor noise and JPEG
    # artefacts on a bright sky otherwise split one continuous trail into dozens of runs.
    vals = np.convolve(vals, np.ones(5, dtype=np.float32) / 5.0, mode="same")
    bg, pk = float(np.percentile(vals, 10)), float(vals.max())
    if pk - bg < 8.0:
        return 0
    on = vals >= bg + 0.30 * (pk - bg)
    return _count_runs(on, min_gap=4, min_run=3)


def _axis_samples(
    s: Streak, shape: tuple[int, ...]
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """Pixel positions along the streak (following its bend), 3 px wide."""
    n = max(8, int(s.length))
    dx = math.cos(math.radians(s.angle_deg))
    dy = math.sin(math.radians(s.angle_deg))
    u = np.linspace(-s.length / 2.0, s.length / 2.0, n + 1)
    c2, c1, c0 = s.bend
    off = c2 * u * u + c1 * u + c0
    xs_all: list[npt.NDArray[np.int64]] = []
    ys_all: list[npt.NDArray[np.int64]] = []
    for o in (-1, 0, 1):
        xs_all.append(np.rint(s.cx + dx * u - dy * (off + o)).astype(np.int64))
        ys_all.append(np.rint(s.cy + dy * u + dx * (off + o)).astype(np.int64))
    xs, ys = np.concatenate(xs_all), np.concatenate(ys_all)
    ok = (xs >= 0) & (xs < shape[1]) & (ys >= 0) & (ys < shape[0])
    return xs[ok], ys[ok]


def new_light_fraction(s: Streak, before: Gray, frame: Gray, after: Gray, threshold: int) -> float:
    """Share of the trail that is brighter in `frame` than in both neighbours."""
    xs, ys = _axis_samples(s, frame.shape)
    if xs.size == 0:
        return 0.0
    here = frame[ys, xs].astype(np.int32)
    others = np.maximum(before[ys, xs], after[ys, xs]).astype(np.int32)
    return float(np.mean(here - others > threshold // 2))


def _count_runs(on: npt.NDArray[np.bool_], min_gap: int, min_run: int) -> int:
    """Runs of True, after closing gaps shorter than `min_gap` and dropping runs shorter
    than `min_run`."""
    runs: list[list[int]] = []
    for i, v in enumerate(on.tolist()):
        if v:
            if runs and i - runs[-1][1] - 1 < min_gap:
                runs[-1][1] = i
            else:
                runs.append([i, i])
    return sum(1 for a, b in runs if b - a + 1 >= min_run)


@dataclass(frozen=True)
class MeteorHit:
    """A confirmed meteor: the frame it is in and its streak."""

    frame: FrameInfo
    image_path: Path
    streak: Streak


@dataclass
class _Pending:
    frame: FrameInfo
    image_path: Path
    streaks: list[Streak]
    dashes: list[int]
    gray: Gray  # the frame the streaks are in
    before: Gray  # and the one before it
    moving: set[int]  # streaks that continue one of the frame before


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
    ) -> None:
        self._cfg = cfg or MeteorConfig()
        self._mask = mask
        # Without a mask: the image circle of the hardware profile, built for the first frame.
        self._mask_frac = mask_radius_frac
        self._prev_gray: Gray | None = None
        self._pending: _Pending | None = None
        self._hotspots: deque[tuple[float, float, float]] = deque(maxlen=2000)

    def reset(self) -> None:
        """Forget the previous frame (mode change, camera restart)."""
        self._prev_gray = None
        self._pending = None

    def _streaks(self, gray: Gray, prev: Gray) -> list[Streak] | None:
        """Candidates in the difference of two frames; None for a cloudy or twinkling frame."""
        cfg = self._cfg
        diff = cv2.absdiff(gray, prev)
        offset = np.uint8(min(255, int(np.median(diff))))
        diff = np.where(diff > offset, diff - offset, 0).astype(np.uint8)  # remove a global offset
        if self._mask is not None:
            diff[~self._mask] = 0
        over = float((diff > cfg.diff_threshold).mean())
        if over > 0.02:
            # A noisy sensor puts many single pixels over the threshold; a 3x3 median
            # removes them but keeps clouds and streaks. Use it when it removes most.
            med = cv2.medianBlur(diff, 3)
            if float((med > cfg.diff_threshold).mean()) < over / 3.0:
                diff = med
        sky = float(self._mask.mean()) if self._mask is not None else 1.0
        if float((diff > cfg.diff_threshold).mean()) / max(sky, 1e-6) > cfg.cloud_fraction:
            return None
        streaks = find_streaks(np.asarray(diff, dtype=np.uint8), cfg)
        if len(streaks) > cfg.scint_max:
            ordered = sorted(streaks, key=lambda st: st.length, reverse=True)
            if ordered[0].length >= cfg.scint_dominance * ordered[1].length:
                return [ordered[0]]
            return None
        return streaks

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
        streaks = self._streaks(gray, prev)
        if streaks is None:  # cloudy or twinkling: nothing from here is trusted
            self._pending = None
            return []

        now = frame.captured_at.timestamp()
        hits: list[MeteorHit] = []
        pending = self._pending
        if pending is not None:
            recent = [h for h in self._hotspots if now - h[2] <= cfg.repeat_window_s]
            for i, (cand, dashes) in enumerate(zip(pending.streaks, pending.dashes, strict=True)):
                if i in pending.moving or any(progressing(cur, cand, cfg) for cur in streaks):
                    continue  # it goes on: satellite or aircraft
                if not any(similar(cur, cand, cfg) for cur in streaks):
                    continue  # no disappearance at the same place: flicker
                repeats = sum(
                    1
                    for hx, hy, _ in recent
                    if math.hypot(hx - cand.cx, hy - cand.cy) <= cfg.repeat_radius_px
                )
                if repeats >= cfg.repeat_k:
                    continue  # a spot that keeps changing
                if cand.length >= cfg.dash_min_len_px and dashes >= cfg.dash_runs:
                    continue  # dashed trail
                light = new_light_fraction(
                    cand, pending.before, pending.gray, gray, cfg.diff_threshold
                )
                if light < cfg.min_new_light:
                    continue  # also there before or after: a drifting star, not a meteor
                hits.append(MeteorHit(pending.frame, pending.image_path, cand))
            for cand in pending.streaks:
                self._hotspots.append((cand.cx, cand.cy, pending.frame.captured_at.timestamp()))

        dashes = [dash_runs(gray, st) if st.length >= cfg.dash_min_len_px else 0 for st in streaks]
        before = pending.streaks if pending is not None else []
        moving = {i for i, st in enumerate(streaks) if any(progressing(b, st, cfg) for b in before)}
        self._pending = (
            _Pending(frame, image_path, streaks, dashes, gray, prev, moving) if streaks else None
        )
        return hits

    def flush(self) -> list[MeteorHit]:
        """End of the night: the last candidates can't be confirmed (no disappearance)."""
        self._pending = None
        return []
