"""Aurora detection (roadmap #5), ported from Benjamin's allsky_aurora module.

At mid latitudes an aurora is a green glow or arc low on the horizon. What sets it apart
from everything else that brightens that part of the night sky is its colour: green
above red *and* above blue. Moonlit or light-polluted cloud is red-dominant (sodium,
moonlight), a twilight or LED dome is blue. Airglow is green too but faint and uniform;
removing the smooth background keeps only structured light.

Without a fisheye calibration (roadmap #8) the agent does not know where the pole is, so
it looks at the whole low ring of the image circle, not only the polar sector.

An aurora lasts minutes to hours: the detector groups candidate frames into an episode
(SPEC §6.4) that opens after `confirm_frames` candidates in a row and closes after a gap.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_agent.calib.solve import Solution, sky_band
from allskyhub_agent.detect.episodes import EpisodeTracker, EpisodeUpdate
from allskyhub_protocol import FrameInfo

Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class AuroraConfig:
    """Defaults of the allsky_aurora module."""

    sun_max_deg: float = -14.0  # in twilight the green test is unreliable
    r_inner: float = 0.60  # the low ring: this part of the image circle radius ...
    r_outer: float = 0.98  # ... up to this
    green_over_red: float = 8.0
    green_over_blue: float = 6.0
    residual_thr: float = 6.0  # above the smooth background: structured light
    dark_floor: float = 24.0
    bright_ceil: float = 252.0
    edge_erode_px: int = 8
    work_width: int = 720
    min_index: float = 0.6  # % of the ring that is aurora-green
    cloud_max: float = 0.85  # overcast: skip
    confirm_frames: int = 2  # candidates in a row before an episode opens
    gap_s: float = 1200.0  # an episode closes after this long without a candidate
    resend_s: float = 300.0  # an open episode is resent at most this often
    # With an orientation (SPEC §4.8): the low sky toward the pole, as in the module.
    pole_alt_lo_deg: float = 2.0
    pole_alt_hi_deg: float = 36.0
    pole_az_half_deg: float = 70.0


@dataclass(frozen=True)
class AuroraScore:
    index: float  # % of the ring
    green: float  # mean green over red of the aurora pixels, 8 bit
    blobs: int
    direction_deg: float | None  # in the image, 0 = up, clockwise
    x: float | None = None  # centroid of the aurora pixels in the frame
    y: float | None = None

    @property
    def candidate(self) -> bool:
        return self.blobs >= 1


def ring_mask(h: int, w: int, radius_frac: float, cfg: AuroraConfig) -> Mask:
    """The low ring of the image circle (radius relative to the short side)."""
    r = radius_frac * min(h, w)
    yy, xx = np.mgrid[0:h, 0:w]
    rho = np.hypot(xx - w / 2.0, yy - h / 2.0)
    return (rho >= cfg.r_inner * r) & (rho <= cfg.r_outer * r)


def score(rgb: npt.NDArray[np.uint8], band: Mask, cfg: AuroraConfig) -> AuroraScore | None:
    """Score the ring for a green, structured, extended glow; None if it is too small."""
    ys, xs = np.nonzero(band)
    if ys.size < 50:
        return None
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    crop = rgb[y0:y1, x0:x1]
    bmask = band[y0:y1, x0:x1].astype(np.uint8) * 255
    ch, cw = crop.shape[:2]
    scale = 1.0
    if cw > cfg.work_width:
        scale = cfg.work_width / float(cw)
        size = (cfg.work_width, max(1, int(ch * scale)))
        crop = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
        bmask = cv2.resize(bmask, size, interpolation=cv2.INTER_NEAREST)
    inb = bmask > 127
    if int(inb.sum()) < 50:
        return None

    crop = cv2.medianBlur(crop, 5)  # stars would read as tiny aurora
    rgb16 = crop.astype(np.int16)
    r, g, b = rgb16[:, :, 0], rgb16[:, :, 1], rgb16[:, :, 2]
    val = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).astype(np.float32)

    # The floor follows the ring's own level, so a dark frame isn't all "empty sky".
    floor = min(cfg.dark_floor, 0.8 * float(np.median(val[inb])))
    # Interior sky only: the rim and the treeline make false structure.
    valid = (inb & (val > floor) & (val < cfg.bright_ceil)).astype(np.uint8) * 255
    k = 2 * cfg.edge_erode_px + 1
    interior = cv2.erode(valid, np.ones((k, k), np.uint8)) > 0
    n_int = int(interior.sum())
    if n_int < 50:
        return None

    kb = max(31, (crop.shape[1] // 12) | 1)
    residual = val - cv2.GaussianBlur(val, (kb, kb), 0)
    gor = g - r
    cand = (
        interior
        & (gor > cfg.green_over_red)
        & (g - b > cfg.green_over_blue)
        & (residual > cfg.residual_thr)
    )
    opened = cv2.morphologyEx(
        cand.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
    )
    num, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    min_blob = max(25, n_int // 150)
    big = [i for i in range(1, int(num)) if int(stats[i, cv2.CC_STAT_AREA]) >= min_blob]
    kept = np.isin(labels, big) if big else np.zeros(labels.shape, dtype=np.bool_)
    n_cand = int(kept.sum())
    index = 100.0 * n_cand / n_int
    if index < cfg.min_index or not big:
        return AuroraScore(round(index, 2), 0.0, 0, None)
    m = cv2.moments(kept.astype(np.uint8), binaryImage=True)
    # Centroid back in the full frame, as a direction from the image centre.
    fx = x0 + float(m["m10"]) / float(m["m00"]) / scale
    fy = y0 + float(m["m01"]) / float(m["m00"]) / scale
    cx, cy = fx - rgb.shape[1] / 2.0, fy - rgb.shape[0] / 2.0
    direction = round(math.degrees(math.atan2(cx, -cy)) % 360.0, 1)
    green = round(float(gor[kept].mean()), 1)
    return AuroraScore(round(index, 2), green, len(big), direction, round(fx, 1), round(fy, 1))


AuroraUpdate = EpisodeUpdate[AuroraScore]


class AuroraDetector:
    """Feed every stored frame; returns the episode updates to send."""

    def __init__(
        self,
        cfg: AuroraConfig | None = None,
        radius_frac: float = 0.48,
        orientation: Callable[[], Solution | None] | None = None,
        latitude: Callable[[], float | None] | None = None,
        sky_mask: Callable[[int, int], Mask | None] | None = None,
    ) -> None:
        self._cfg = cfg or AuroraConfig()
        self._radius = radius_frac
        self._orientation = orientation
        self._latitude = latitude
        self._sky_mask = sky_mask
        self._band: Mask | None = None
        self._band_key: tuple[object, ...] | None = None
        self._episodes: EpisodeTracker[AuroraScore] = EpisodeTracker(
            self._cfg.confirm_frames, self._cfg.gap_s, self._cfg.resend_s
        )

    def flush(self) -> list[AuroraUpdate]:
        return self._episodes.close()

    def feed(
        self,
        frame: FrameInfo,
        rgb: npt.NDArray[np.uint8],
        image_path: Path,
        cloud_cover: float | None = None,
    ) -> list[AuroraUpdate]:
        cfg = self._cfg
        out = self._episodes.tick(frame)
        if frame.sun_elevation > cfg.sun_max_deg or rgb.ndim != 3:
            return out + self._episodes.close()
        if cloud_cover is not None and cloud_cover > cfg.cloud_max:
            return out  # overcast says nothing; the gap closes the episode
        h, w = rgb.shape[:2]
        s = score(rgb, self._band_for(h, w), cfg)
        if s is None or not s.candidate:
            self._episodes.miss()
            return out
        return out + self._episodes.hit(frame, image_path, s)

    def _band_for(self, h: int, w: int) -> Mask:
        """The polar sector with an orientation, else the whole low ring."""
        cfg = self._cfg
        sol = self._orientation() if self._orientation is not None else None
        lat = self._latitude() if self._latitude is not None else None
        sky = self._sky_mask(h, w) if self._sky_mask is not None else None
        if sol is not None and sol.width and (w, h) != (sol.width, sol.height):
            sol = None
        key = (h, w, sol, lat is not None and lat < 0, id(sky))
        if self._band is None or key != self._band_key:
            if sol is not None and lat is not None:
                pole = 180.0 if lat < 0 else 0.0
                band = sky_band(sol, cfg.pole_alt_lo_deg, cfg.pole_alt_hi_deg, pole,
                                cfg.pole_az_half_deg, h, w)  # fmt: skip
                if sky is not None:
                    band &= sky
            else:
                band = ring_mask(h, w, self._radius, cfg)
            self._band, self._band_key = band, key
        return self._band
