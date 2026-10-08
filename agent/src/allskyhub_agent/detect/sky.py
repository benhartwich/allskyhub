"""Sky condition per frame (SPEC §6.3 `sky`), ported from Benjamin's allsky_skyquality module.

- Stars: template matching against a small blurred disc (the indi-allsky method).
- Cloud cover at night: the share of cells in the upper sky without a star. Near the
  horizon trees, haze and the image circle's edge blank cells on clear nights too, so only
  the inner part of the image circle counts.
- No cloud cover by day yet: the blue/red ratio of the module fails with cameras without
  an IR cut filter (an overcast sky stays bluish on the ASI678MC); null until a method
  works on real frames.
- SQM: mean background normalised by exposure and gain, `mag = offset - 2.5 log10(signal)`.
  Gain is in 0.1 dB in every profile, so the gain factor is 10^(gain/200). The offset of
  the ZWO ASI678MC profile (18.8) is the module's calibration against a dark sky.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_agent.core.metering import circle_mask
from allskyhub_protocol import FrameInfo, SkyMetrics

Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class SkyConfig:
    sqm_offset: float = 18.8
    star_threshold: float = 0.65  # template match score for a star
    cells_across: int = 16  # cells across the short image side
    inner_frac: float = 0.75  # cloud cells only within this part of the circle radius
    night_sun_deg: float = -12.0  # star based values at or below this sun elevation
    sqm_sun_deg: float = -18.0  # SQM only at astronomical night


_template: npt.NDArray[np.uint8] | None = None


def _star_template() -> npt.NDArray[np.uint8]:
    global _template
    if _template is None:
        t = np.zeros((15, 15), np.uint8)
        cv2.circle(t, (7, 7), 3, 255, cv2.FILLED)
        _template = np.asarray(cv2.blur(t, (2, 2)), dtype=np.uint8)
    return _template


def star_points(
    gray: npt.NDArray[np.uint8], mask: Mask, threshold: float
) -> tuple[npt.NDArray[np.intp], npt.NDArray[np.intp]]:
    """(xs, ys) of star centres with a match score of at least `threshold`."""
    img = np.where(mask, gray, 0).astype(np.uint8)
    match = cv2.matchTemplate(img, _star_template(), cv2.TM_CCOEFF_NORMED)
    ys, xs = np.nonzero(match >= threshold)
    return xs + 7, ys + 7  # the match image is offset by half the template


def count_stars(xs: npt.NDArray[np.intp], ys: npt.NDArray[np.intp]) -> int:
    """Stars, with points in the same 10 px cell counted once."""
    if xs.size == 0:
        return 0
    return int(np.unique((ys // 10).astype(np.int64) * 1_000_003 + xs // 10).size)


def night_cloud_cover(
    shape: tuple[int, int], inner: Mask, xs: npt.NDArray[np.intp], ys: npt.NDArray[np.intp],
    cells_across: int,
) -> float | None:  # fmt: skip
    """Share of the inner sky cells without a star."""
    h, w = shape
    cell = max(8, min(h, w) // cells_across)
    gh, gw = h // cell, w // cell
    if gh == 0 or gw == 0:
        return None
    # A cell is sky if most of it lies within the inner circle.
    frac = inner[: gh * cell, : gw * cell].reshape(gh, cell, gw, cell).mean(axis=(1, 3))
    sky = frac > 0.5
    total = int(sky.sum())
    if total == 0:
        return None
    stars = np.zeros((gh, gw), dtype=np.bool_)
    ok = (ys < gh * cell) & (xs < gw * cell)
    stars[ys[ok] // cell, xs[ok] // cell] = True
    return round(1.0 - int((sky & stars).sum()) / total, 3)


def sqm(mean_adu: float, exposure_us: int, gain: float, offset: float) -> float | None:
    """mag/arcsec² from the mean background (8 bit), exposure and gain (0.1 dB)."""
    if mean_adu <= 0 or exposure_us <= 0:
        return None
    signal = mean_adu / (exposure_us / 1e6) / 10.0 ** (gain / 200.0)
    return round(offset - 2.5 * math.log10(signal), 2)


class SkyMeter:
    """Measures every frame it is given; `latest` is what status reports."""

    def __init__(self, cfg: SkyConfig | None = None, radius_frac: float = 0.48) -> None:
        self._cfg = cfg or SkyConfig()
        self._radius = radius_frac
        self._masks: dict[tuple[int, int], tuple[Mask, Mask]] = {}
        self._learned: Mask | None = None
        self._lock = threading.Lock()
        self._latest: SkyMetrics | None = None

    @property
    def latest(self) -> SkyMetrics | None:
        with self._lock:
            return self._latest

    def set_mask(self, mask: Mask) -> None:
        """Use the learned sky mask (SPEC §4.7): trees and roofs are not cloud."""
        self._learned = mask
        self._masks = {}

    def _mask(self, h: int, w: int) -> tuple[Mask, Mask]:
        if (h, w) not in self._masks:
            inner = circle_mask(h, w, self._radius * self._cfg.inner_frac)
            learned = self._learned
            if learned is not None and learned.shape == (h, w):
                self._masks[(h, w)] = (learned, learned & inner)
            else:
                self._masks[(h, w)] = (circle_mask(h, w, self._radius), inner)
        return self._masks[(h, w)]

    def measure(self, frame: FrameInfo, image: npt.NDArray[np.uint8]) -> SkyMetrics:
        """`image` RGB or gray, unblurred: a blur turns noise into star-like blobs."""
        cfg = self._cfg
        gray: npt.NDArray[np.uint8] = (
            np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
            if image.ndim == 3
            else image
        )
        h, w = gray.shape
        mask, inner = self._mask(h, w)
        sun = frame.sun_elevation
        cloud: float | None = None
        stars: int | None = None
        mag: float | None = None
        if sun <= cfg.night_sun_deg:
            xs, ys = star_points(gray, mask, cfg.star_threshold)
            stars = count_stars(xs, ys)
            up = inner[ys, xs]
            cloud = night_cloud_cover((h, w), inner, xs[up], ys[up], cfg.cells_across)
            if sun <= cfg.sqm_sun_deg:
                # The mean over the sky, as the offset was calibrated with.
                mag = sqm(float(gray[mask].mean()), frame.exposure_us, frame.gain, cfg.sqm_offset)
        m = SkyMetrics(
            at=frame.captured_at,
            night_id=frame.night_id,
            cloud_cover=cloud,
            sqm_mag=mag,
            stars=stars,
        )
        with self._lock:
            self._latest = m
        return m
