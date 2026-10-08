"""Lightning detection (roadmap #5), ported from Benjamin's allsky_lightning module.

A flash lights up a large part of the sky (clouds, the horizon) for a moment. In the
difference to the previous frame it is a wide area of new light. Drifting clouds and the
dusk ramp make such areas too, so a flash is confirmed only one frame later: the lit area
must be brighter than in the frame before *and* the frame after. Clouds that drift in stay
lit; a flash is gone. Night only, with the sun well below the horizon: the module's real
nights showed false alarms down to -7° from the dusk ramp.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import numpy.typing as npt

from allskyhub_agent.core.metering import circle_mask
from allskyhub_protocol import FrameInfo, Mode

Gray = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class LightningConfig:
    flash_delta: int = 18  # brightening (8 bit) for a pixel to count as lit
    # This share of the sky must light up together; a bright meteor (a thin streak) stays
    # well below it.
    min_area_frac: float = 0.005
    max_sun_deg: float = -12.0  # nautical dusk; the module saw dusk false alarms at -7°
    min_transient: float = 0.6  # this share of the lit area must be dark again afterwards
    exposure_tolerance: float = 0.05  # frames with other exposure/gain are not compared
    storm_window_s: float = 1800.0


@dataclass(frozen=True)
class Flash:
    area_frac: float  # 0..1 of the sky
    peak: float  # 0..1, mean brightening of the lit area


@dataclass(frozen=True)
class LightningHit:
    frame: FrameInfo
    image_path: Path
    flash: Flash
    storm_flashes: int  # flashes in the last storm_window_s, this one included
    storm_start: datetime  # first flash of the storm: no gap longer than storm_window_s


@dataclass
class _Pending:
    frame: FrameInfo
    image_path: Path
    flash: Flash
    lit: Mask
    gray: Gray
    before: Gray


def _same_exposure(a: FrameInfo, b: FrameInfo, tol: float) -> bool:
    return abs(a.exposure_us - b.exposure_us) <= tol * max(a.exposure_us, b.exposure_us) and abs(
        a.gain - b.gain
    ) <= tol * max(abs(a.gain), abs(b.gain), 1.0)


class LightningDetector:
    """Feed every stored frame; returns the flashes confirmed for the frame before."""

    def __init__(
        self,
        cfg: LightningConfig | None = None,
        mask: Mask | None = None,
        mask_radius_frac: float | None = None,
    ) -> None:
        self._cfg = cfg or LightningConfig()
        self._mask = mask
        self._mask_frac = mask_radius_frac
        self._prev: tuple[FrameInfo, Gray] | None = None
        self._pending: _Pending | None = None
        self._flashes: deque[float] = deque(maxlen=1000)
        self._storm_start: datetime | None = None

    def set_mask(self, mask: Mask) -> None:
        """Use the learned sky mask (SPEC §4.7) instead of the image circle."""
        self._mask = mask

    def reset(self) -> None:
        self._prev = None
        self._pending = None

    def _flash(self, gray: Gray, prev: Gray) -> tuple[Flash, Mask] | None:
        cfg = self._cfg
        diff = gray.astype(np.int16) - prev.astype(np.int16)
        diff -= np.int16(np.median(diff))  # a global offset (moon, noise floor) is no flash
        lit = diff >= cfg.flash_delta
        if self._mask is not None:
            lit &= self._mask
        sky = int(self._mask.sum()) if self._mask is not None else lit.size
        area = float(lit.sum()) / max(sky, 1)
        if area < cfg.min_area_frac:
            return None
        peak = float(diff[lit].mean()) / 255.0
        return Flash(area_frac=round(area, 4), peak=round(min(1.0, peak), 3)), lit

    def feed(self, frame: FrameInfo, gray: Gray, image_path: Path) -> list[LightningHit]:
        cfg = self._cfg
        if frame.mode is not Mode.NIGHT or frame.sun_elevation > cfg.max_sun_deg:
            self.reset()
            return []
        if self._mask is not None and self._mask.shape != gray.shape:
            self._mask = None
        if self._mask is None and self._mask_frac is not None:
            self._mask = circle_mask(gray.shape[0], gray.shape[1], self._mask_frac)
        prev, self._prev = self._prev, (frame, gray)

        hits: list[LightningHit] = []
        pending, self._pending = self._pending, None
        if pending is not None and pending.gray.shape == gray.shape:
            # The frame after may be darker (auto exposure reacts to the flash), never
            # brighter at the lit place: brighter than before *and* after = a flash.
            here = pending.gray[pending.lit].astype(np.int16)
            others = np.maximum(pending.before[pending.lit], gray[pending.lit]).astype(np.int16)
            if float(np.mean(here - others >= cfg.flash_delta // 2)) >= cfg.min_transient:
                at = pending.frame.captured_at
                t = at.timestamp()
                if self._storm_start is None or t - self._flashes[-1] > cfg.storm_window_s:
                    self._storm_start = at
                self._flashes.append(t)
                storm = sum(1 for f in self._flashes if t - f <= cfg.storm_window_s)
                hits.append(
                    LightningHit(
                        pending.frame, pending.image_path, pending.flash, storm, self._storm_start
                    )
                )

        if prev is None or prev[1].shape != gray.shape:
            return hits
        if not _same_exposure(prev[0], frame, cfg.exposure_tolerance):
            return hits  # an exposure step brightens everything
        found = self._flash(gray, prev[1])
        if found is not None:
            self._pending = _Pending(frame, image_path, found[0], found[1], gray, prev[1])
        return hits

    def flush(self) -> list[LightningHit]:
        """End of the night: the last candidate can't be confirmed."""
        self.reset()
        return []
