"""Noctilucent clouds (roadmap #8), ported from Benjamin's allsky_nlc module.

NLC are night-shining clouds at 80 km, lit by the sun from below the horizon: in deep
twilight (sun -16° to -9°) they stand as electric-blue, structured filaments in the low
sky toward the sun, against a sky that is already dark there. The band to look at needs
the image orientation (SPEC §4.8): without it the module found only false alarms.

A pixel counts if it is brighter than the smooth twilight background, blue above red,
and part of an extended blob. The frame counts only if the band's interior is dark
(`bg_ceil`): twilight haze and low cloud are lit broadly and were all of the module's
false alarms in July 2026. Candidates form episodes like aurora (SPEC §6.4).
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_agent.calib.solve import Solution, sky_band
from allskyhub_agent.core.sun import sun_position
from allskyhub_agent.detect.episodes import EpisodeTracker, EpisodeUpdate
from allskyhub_protocol import FrameInfo

Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class NlcConfig:
    """Defaults of the allsky_nlc module v0.2.0."""

    sun_lo_deg: float = -16.0  # below: the NLC themselves are in the earth's shadow
    sun_hi_deg: float = -9.0  # above: the low sunward sky is too bright
    alt_lo_deg: float = 12.0
    alt_hi_deg: float = 45.0
    az_half_deg: float = 75.0
    blue_excess: float = 8.0
    residual_thr: float = 10.0
    dark_floor: float = 30.0
    bright_ceil: float = 245.0
    bg_ceil: float = 130.0
    edge_erode_px: int = 8
    work_width: int = 720
    min_index: float = 1.0
    confirm_frames: int = 2
    gap_s: float = 1200.0
    resend_s: float = 300.0


@dataclass(frozen=True)
class NlcScore:
    index: float  # % of the band's interior
    blue: float  # mean blue over red of the NLC pixels, 8 bit
    blobs: int
    background: float  # median brightness of the band's interior
    direction_deg: float | None = None
    x: float | None = None
    y: float | None = None


NlcUpdate = EpisodeUpdate[NlcScore]


def band_mask(sol: Solution, sun_az: float, h: int, w: int, cfg: NlcConfig) -> Mask:
    """The low sky toward the sun: altitude alt_lo..alt_hi, azimuth sun ± az_half."""
    return sky_band(sol, cfg.alt_lo_deg, cfg.alt_hi_deg, sun_az, cfg.az_half_deg, h, w)


def score(rgb: npt.NDArray[np.uint8], band: Mask, cfg: NlcConfig) -> NlcScore | None:
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
    crop = cv2.medianBlur(crop, 5)  # stars are bluish points that would look like NLC
    rgb16 = crop.astype(np.int16)
    r, b = rgb16[:, :, 0], rgb16[:, :, 2]
    val = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).astype(np.float32)
    # Interior sky only: the bright edge against trees and the vignette is no NLC.
    valid = (inb & (val > cfg.dark_floor) & (val < cfg.bright_ceil)).astype(np.uint8) * 255
    k = 2 * cfg.edge_erode_px + 1
    interior = cv2.erode(valid, np.ones((k, k), np.uint8)) > 0
    n_int = int(interior.sum())
    if n_int < 50:
        return None
    kb = max(31, (crop.shape[1] // 12) | 1)
    residual = val - cv2.GaussianBlur(val, (kb, kb), 0)
    blue = b - r
    cand = interior & (residual > cfg.residual_thr) & (blue > cfg.blue_excess)
    opened = cv2.morphologyEx(
        cand.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
    )
    num, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    min_blob = max(25, n_int // 150)
    big = [i for i in range(1, int(num)) if int(stats[i, cv2.CC_STAT_AREA]) >= min_blob]
    kept = np.isin(labels, big) if big else np.zeros(labels.shape, dtype=np.bool_)
    n_cand = int(kept.sum())
    index = round(100.0 * n_cand / n_int, 2)
    bg = round(float(np.median(val[interior])), 1)
    if index < cfg.min_index or not big or bg > cfg.bg_ceil:
        return NlcScore(index, 0.0, 0, bg)
    m = cv2.moments(kept.astype(np.uint8), binaryImage=True)
    fx = x0 + float(m["m10"]) / float(m["m00"]) / scale
    fy = y0 + float(m["m01"]) / float(m["m00"]) / scale
    cx, cy = fx - rgb.shape[1] / 2.0, fy - rgb.shape[0] / 2.0
    direction = round(math.degrees(math.atan2(cx, -cy)) % 360.0, 1)
    return NlcScore(index, round(float(blue[kept].mean()), 1), len(big), bg, direction,
                    round(fx, 1), round(fy, 1))  # fmt: skip


class NlcDetector:
    """Feed every stored frame; needs the orientation, the location and the sky mask."""

    def __init__(
        self,
        orientation: Callable[[], Solution | None],
        location: Callable[[], tuple[float, float] | None],
        sky_mask: Callable[[int, int], Mask | None] | None = None,
        cfg: NlcConfig | None = None,
    ) -> None:
        self._cfg = cfg or NlcConfig()
        self._orientation = orientation
        self._location = location
        self._sky_mask = sky_mask
        self._episodes: EpisodeTracker[NlcScore] = EpisodeTracker(
            self._cfg.confirm_frames, self._cfg.gap_s, self._cfg.resend_s
        )

    def flush(self) -> list[NlcUpdate]:
        return self._episodes.close()

    def feed(
        self, frame: FrameInfo, rgb: npt.NDArray[np.uint8], image_path: Path
    ) -> list[NlcUpdate]:
        cfg = self._cfg
        out = self._episodes.tick(frame)
        sun = frame.sun_elevation
        if not cfg.sun_lo_deg <= sun <= cfg.sun_hi_deg or rgb.ndim != 3:
            return out + self._episodes.close()
        sol, loc = self._orientation(), self._location()
        h, w = rgb.shape[:2]
        if sol is None or loc is None or (sol.width and (w, h) != (sol.width, sol.height)):
            return out
        mid = frame.captured_at + timedelta(microseconds=frame.exposure_us // 2)
        _, sun_az = sun_position(mid, loc[0], loc[1])
        band = band_mask(sol, sun_az, h, w, cfg)
        sky = self._sky_mask(h, w) if self._sky_mask is not None else None
        if sky is not None:
            band &= sky
        s = score(rgb, band, cfg)
        if s is None or s.blobs == 0:
            self._episodes.miss()
            return out
        return out + self._episodes.hit(frame, image_path, s)
