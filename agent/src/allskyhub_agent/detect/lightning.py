"""Lightning detection (roadmap #5), ported from Benjamin's allsky_lightning module.

A flash lights up a large part of the sky (clouds, the horizon) for a moment. In the
difference to the previous frame it is a wide area of new light. Drifting clouds and the
dusk ramp make such areas too, so a flash is confirmed only one frame later: the lit area
must be brighter than in the frame before *and* the frame after. Clouds that drift in stay
lit; a flash is gone. Night only, with the sun well below the horizon: the module's real
nights showed false alarms down to -7° from the dusk ramp.

A lamp near the camera (a neighbour's motion-sensor light) also makes such flashes, again
and again at the same place and in its own colour. The light source check (SPEC §6.4,
roadmap L35) marks a lamp-coloured flash that lights the same place as an earlier
lamp-coloured flash of the storm; colour alone or place alone also hits real storms.
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
Rgb = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]
STEP = 4  # colour on every 4th pixel: the added light's mix, not its detail


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
    # SPEC §6.4 Light source check: "label" (default), "drop" or "off".
    light_source: str = "label"
    lamp_green_max: float = 0.28  # share of the added light: the test camera's magenta LED
    lamp_blue_min: float = 0.37
    lamp_iou: float = 0.3  # lit areas on the GRID
    lamp_near_frac: float = 0.04  # centroids this close, as a share of the frame width


GRID = (135, 240)  # rows, columns: the lit areas compared for the light source check


@dataclass(frozen=True)
class Flash:
    area_frac: float  # 0..1 of the sky
    peak: float  # 0..1, mean brightening of the lit area
    x: float = 0.0  # centroid of the lit area in the frame
    y: float = 0.0


@dataclass(frozen=True)
class LightningHit:
    frame: FrameInfo
    image_path: Path
    flash: Flash
    storm_flashes: int  # flashes in the last storm_window_s, this one included
    storm_start: datetime  # first flash of the storm: no gap longer than storm_window_s
    seq: int = 0  # running number of the confirmed flashes, for `also`
    light_source: bool = False  # SPEC §6.4 Light source check
    also: tuple[int, ...] = ()  # earlier flashes (by seq) found to be the same light source


@dataclass
class _Lamp:
    """A lamp-coloured flash of the current storm."""

    seq: int
    grid: Mask
    x: float
    y: float
    marked: bool


@dataclass
class _Pending:
    frame: FrameInfo
    image_path: Path
    flash: Flash
    lit: Mask
    gray: Gray
    before: Gray
    lamp_coloured: bool = False


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
        self._small: Rgb | None = None  # the frame before, every 4th pixel (colour check)
        self._lamps: list[_Lamp] = []
        self._last_any: float | None = None
        self._seq = 0

    def set_mask(self, mask: Mask) -> None:
        """Use the learned sky mask (SPEC §4.7) instead of the image circle."""
        self._mask = mask

    def reset(self) -> None:
        self._prev = None
        self._pending = None
        self._small = None

    def _lamp_coloured(self, rgb: Rgb | None, before: Rgb | None, lit: Mask) -> bool:
        """SPEC §6.4: the added light over the lit area is mostly red and blue."""
        cfg = self._cfg
        if cfg.light_source == "off" or rgb is None or before is None:
            return False
        small_lit = lit[::STEP, ::STEP]
        if small_lit.shape != rgb.shape[:2] or not small_lit.any():
            return False
        added = rgb[small_lit].astype(np.int32) - before[small_lit].astype(np.int32)
        sums = added.sum(axis=0)
        total = int(sums.sum())
        if total <= 0:
            return False
        return (
            int(sums[1]) / total <= cfg.lamp_green_max and int(sums[2]) / total >= cfg.lamp_blue_min
        )

    def _light_source(self, p: _Pending, seq: int) -> tuple[bool, tuple[int, ...]]:
        """A lamp-coloured flash at the place of an earlier one of this storm (SPEC §6.4).
        Returns whether it is a light source and the earlier ones that now are too."""
        if not p.lamp_coloured:
            return False, ()
        cfg = self._cfg
        h, w = p.lit.shape
        rows = (np.arange(GRID[0]) * h // GRID[0]).astype(np.intp)
        cols = (np.arange(GRID[1]) * w // GRID[1]).astype(np.intp)
        grid: Mask = p.lit[np.ix_(rows, cols)]
        near = cfg.lamp_near_frac * w
        same: list[_Lamp] = []
        for lamp in self._lamps:
            union = int(np.logical_or(grid, lamp.grid).sum())
            iou = int(np.logical_and(grid, lamp.grid).sum()) / union if union else 0.0
            dist = float(np.hypot(p.flash.x - lamp.x, p.flash.y - lamp.y))
            if iou >= cfg.lamp_iou or dist <= near:
                same.append(lamp)
        also = tuple(lamp.seq for lamp in same if not lamp.marked)
        for lamp in same:
            lamp.marked = True
        self._lamps.append(_Lamp(seq, grid, p.flash.x, p.flash.y, marked=bool(same)))
        return bool(same), also

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
        ys, xs = np.nonzero(lit)
        flash = Flash(
            area_frac=round(area, 4),
            peak=round(min(1.0, peak), 3),
            x=round(float(xs.mean()), 1),
            y=round(float(ys.mean()), 1),
        )
        return flash, lit

    def feed(
        self, frame: FrameInfo, gray: Gray, image_path: Path, rgb: Rgb | None = None
    ) -> list[LightningHit]:
        """`rgb` (the same frame in colour) enables the light source check."""
        cfg = self._cfg
        if frame.mode is not Mode.NIGHT or frame.sun_elevation > cfg.max_sun_deg:
            self.reset()
            return []
        small_before, self._small = (
            self._small,
            (rgb[::STEP, ::STEP] if rgb is not None and cfg.light_source != "off" else None),
        )
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
                hit = self._confirmed(pending)
                if hit is not None:
                    hits.append(hit)

        if prev is None or prev[1].shape != gray.shape:
            return hits
        if not _same_exposure(prev[0], frame, cfg.exposure_tolerance):
            return hits  # an exposure step brightens everything
        found = self._flash(gray, prev[1])
        if found is not None:
            small = self._small
            coloured = self._lamp_coloured(small, small_before, found[1])
            self._pending = _Pending(frame, image_path, found[0], found[1], gray, prev[1],
                                     coloured)  # fmt: skip
        return hits

    def _confirmed(self, p: _Pending) -> LightningHit | None:
        cfg = self._cfg
        at = p.frame.captured_at
        t = at.timestamp()
        new_storm = self._storm_start is None or t - self._flashes[-1] > cfg.storm_window_s
        # The lamps are remembered across dropped flashes too, else a lamp that lights up
        # alone would start a new "storm" each time and never be found again.
        if self._last_any is None or t - self._last_any > cfg.storm_window_s:
            self._lamps = []
        self._last_any = t
        self._seq += 1
        lamp, also = self._light_source(p, self._seq)
        if lamp and cfg.light_source == "drop":
            return None  # SPEC §6.4: no event, neither starts nor extends a storm
        start = at if new_storm or self._storm_start is None else self._storm_start
        self._storm_start = start
        self._flashes.append(t)
        storm = sum(1 for f in self._flashes if t - f <= cfg.storm_window_s)
        return LightningHit(p.frame, p.image_path, p.flash, storm, start, self._seq, lamp,
                            also)  # fmt: skip

    def flush(self) -> list[LightningHit]:
        """End of the night: the last candidate can't be confirmed."""
        self.reset()
        return []
