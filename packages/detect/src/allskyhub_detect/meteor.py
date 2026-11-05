"""Meteor detection (roadmap #1), the part without memory: streaks in the difference of
two frames and the checks of one frame and its neighbours, shared by the camera agent
(allskyhub_agent.detect.meteor, which follows streaks from frame to frame) and the hub
(SharedSky stations, SPEC §10). Ported from Benjamin's allsky_meteordetect module.

Temporal approach: the difference to the previous frame keeps only new light, so stars and
static clouds cancel out. Long, thin streaks in it are candidates. A meteor is in exactly
one frame, so it shows up in two consecutive difference images at the same place: when it
appears and when it disappears; a satellite or aircraft goes on in the next frame.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_detect import trail

Gray = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]


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
    # Trail verdicts (SPEC §6.4), the module's defaults. Short streaks only for the star
    # checks: a long, bright fireball may well pass a star.
    short_max_px: float = 130.0
    star_trail_tol_deg: float = 12.0
    star_radius_px: float = 16.0
    glare_radii: float = 10.0
    glare_tol_deg: float = 6.0
    frag_min_len_px: float = 120.0
    frag_min: int = 5
    edge_min_len_px: float = 80.0
    edge_max_elongation: float = 10.0
    edge_margin_px: float = 50.0


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
        if dy < 0 or (dy == 0 and dx < 0):
            # The axis as `angle_deg` gives it (0..180), so the bend below has the same
            # sides wherever it is read back (axis_samples, dash_runs).
            dx, dy = -dx, -dy
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


def angle_diff(a: float, b: float) -> float:
    """The angle between two axes (0..180 degrees, no sign), 0..90."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def progressing(a: Streak, b: Streak, cfg: MeteorConfig) -> bool:
    """b continues a: a satellite or aircraft seen in two frames."""
    dc = math.hypot(a.cx - b.cx, a.cy - b.cy)
    return (
        cfg.progress_min_px < dc < cfg.progress_max_px
        and angle_diff(a.angle_deg, b.angle_deg) < cfg.progress_angle_deg
    )


def similar(a: Streak, b: Streak, cfg: MeteorConfig) -> bool:
    """b is a at the same place: a meteor's appearance and its disappearance."""
    return (
        math.hypot(a.cx - b.cx, a.cy - b.cy) < cfg.similar_px
        and angle_diff(a.angle_deg, b.angle_deg) < cfg.similar_angle_deg
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


def centre_line(s: Streak, n: int) -> list[tuple[float, float]]:
    """`n` evenly spaced pixels on the streak's centre line (following its bend), from
    `p1` to `p2` (SPEC §6.4 `track`)."""
    ex, ey = s.p2[0] - s.p1[0], s.p2[1] - s.p1[1]
    length = math.hypot(ex, ey)
    if length < 1e-9 or n < 2:
        return [s.p1, s.p2][: max(n, 0)]
    dx, dy = ex / length, ey / length  # the axis the bend was fitted along (p1 -> p2)
    u0 = (s.p1[0] - s.cx) * dx + (s.p1[1] - s.cy) * dy
    u1 = (s.p2[0] - s.cx) * dx + (s.p2[1] - s.cy) * dy
    c2, c1, c0 = s.bend
    out: list[tuple[float, float]] = []
    for i in range(n):
        u = u0 + (u1 - u0) * i / (n - 1)
        v = c2 * u * u + c1 * u + c0
        out.append((s.cx + dx * u - dy * v, s.cy + dy * u + dx * v))
    return out


def axis_samples(
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
    xs, ys = axis_samples(s, frame.shape)
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


def difference_streaks(
    gray: Gray, prev: Gray, cfg: MeteorConfig, mask: Mask | None = None
) -> tuple[list[Streak], Gray] | None:
    """Candidates in the difference of two frames of the same size, and that difference;
    None for a cloudy or twinkling frame (nothing from it is trusted)."""
    diff = cv2.absdiff(gray, prev)
    offset = np.uint8(min(255, int(np.median(diff))))
    diff = np.where(diff > offset, diff - offset, 0).astype(np.uint8)  # remove a global offset
    if mask is not None:
        diff[~mask] = 0
    over = float((diff > cfg.diff_threshold).mean())
    if over > 0.02:
        # A noisy sensor puts many single pixels over the threshold; a 3x3 median
        # removes them but keeps clouds and streaks. Use it when it removes most.
        med = cv2.medianBlur(diff, 3)
        if float((med > cfg.diff_threshold).mean()) < over / 3.0:
            diff = med
    sky = float(mask.mean()) if mask is not None else 1.0
    if float((diff > cfg.diff_threshold).mean()) / max(sky, 1e-6) > cfg.cloud_fraction:
        return None
    diff = np.asarray(diff, dtype=np.uint8)
    streaks = find_streaks(diff, cfg)
    if len(streaks) > cfg.scint_max:
        ordered = sorted(streaks, key=lambda st: st.length, reverse=True)
        if ordered[0].length >= cfg.scint_dominance * ordered[1].length:
            return [ordered[0]], diff
        return None
    return streaks, diff


# A pixel track (``Candidate.track_px``) gets a point about every this many pixels, 2 to 32
# of them (SPEC §6.4 `track`; the camera spaces its sky track about 1° apart).
TRACK_PX_SPACING = 20.0
TRACK_POINTS = (2, 32)


@dataclass(frozen=True)
class Candidate:
    """A meteor in ``frame`` by the checks of one frame and its neighbours (``candidates``)."""

    streak: Streak
    track_px: list[tuple[float, float]]  # along the centre line, from ``streak.p1`` to ``p2``
    peak: float  # 0..1, the brightest new light on the streak, the camera's scale
    length_px: float
    angle_deg: float  # axis angle from the image x axis, 0..180
    new_light: float  # share of the trail brighter than in both neighbours
    dash_runs: int  # bright segments along it in the frame (0 when it is short)


def track_px(s: Streak, n: int | None = None) -> list[tuple[float, float]]:
    """The streak's centre line as ``n`` points, by default one about every
    ``TRACK_PX_SPACING`` pixels, 2 to 32."""
    if n is None:
        n = math.ceil(s.length / TRACK_PX_SPACING) + 1
    return centre_line(s, min(TRACK_POINTS[1], max(TRACK_POINTS[0], n)))


def candidates(
    before: Gray,
    frame: Gray,
    after: Gray,
    mask: Mask | None = None,
    cfg: MeteorConfig | None = None,
) -> list[Candidate]:
    """The meteors in ``frame``, from it and the frames just before and after it (gray, of
    one size and exposure, e.g. ``to_gray`` of each), with the camera's acceptance of such
    a triplet:

    - the streak appears in ``frame - before`` and disappears at the same place in
      ``after - frame`` (``similar``), and nothing in ``after - frame`` continues it
      (``progressing``: a satellite or aircraft goes on);
    - neither difference is cloudy or twinkling (``difference_streaks``);
    - at least ``min_new_light`` of its trail is brighter in ``frame`` than in both
      neighbours (a slowly drifting star is in all three);
    - the single-frame trail verdicts: not ``glare``, ``dashed``, ``fragmented`` or
      ``edge_glow`` (that one only with a ``mask``).

    Not applied, because they need more than three frames or the sky (the camera agent
    does them): a streak continuing one of the frame before ``before`` (``moving``), the
    same great circle as a streak of a neighbouring frame (``circle``), a spot that keeps
    changing over 25 minutes (``repeat``), a star or the direction of star trails at the
    place (``star``, ``star_trail``), and matching aircraft positions. So a hub that runs
    this on another program's frames accepts a little more than the camera does."""
    cfg = cfg or MeteorConfig()
    if not (before.shape == frame.shape == after.shape):
        return []
    if mask is not None and mask.shape != frame.shape:
        mask = None  # a different resolution: detect on the whole frame, as the camera does
    first = difference_streaks(frame, before, cfg, mask)
    second = difference_streaks(after, frame, cfg, mask)
    if first is None or second is None:
        return []
    streaks, diff = first
    later, _ = second
    edge = trail.edge_distance(mask) if mask is not None else None
    glare = trail.glare_sources(frame)
    out: list[Candidate] = []
    for cand in streaks:
        if any(progressing(cur, cand, cfg) for cur in later):
            continue  # it goes on: satellite or aircraft
        if not any(similar(cur, cand, cfg) for cur in later):
            continue  # no disappearance at the same place: flicker
        light = new_light_fraction(cand, before, frame, after, cfg.diff_threshold)
        if light < cfg.min_new_light:
            continue
        dashes = dash_runs(frame, cand) if cand.length >= cfg.dash_min_len_px else 0
        if trail.glare_spike(cand, glare, cfg.glare_radii, cfg.glare_tol_deg):
            continue
        if cand.length >= cfg.dash_min_len_px and dashes >= cfg.dash_runs:
            continue
        if (
            cand.length >= cfg.frag_min_len_px
            and trail.collinear_fragments(diff, cand, cfg.diff_threshold) >= cfg.frag_min
        ):
            continue
        if (
            edge is not None
            and cand.length >= cfg.edge_min_len_px
            and cand.elongation < cfg.edge_max_elongation
            and trail.edge_hug(cand, edge) < cfg.edge_margin_px
        ):
            continue
        out.append(Candidate(
            streak=cand, track_px=track_px(cand), peak=cand.peak, length_px=cand.length,
            angle_deg=cand.angle_deg, new_light=light, dash_runs=dashes,
        ))  # fmt: skip
    return out
