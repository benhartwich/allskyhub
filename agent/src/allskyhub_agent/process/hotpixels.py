"""Hot pixels without dark frames (SPEC §4.6).

A dark frame needs the lens covered, which a plug-and-play camera on the roof can't ask
for. Hot pixels show up anyway: in the minimum of many night frames taken hours apart,
stars and clouds have moved away and only light that is always at the same pixel is
left. A hot pixel is a small spot there that is much brighter than its surroundings.
On Benjamin's ASI678MC about 900 such pixels were the same in two nights.

Single nights also flag a few thousand pixels that are not defects (stars close to the
pole hardly move, a cloud bank that stays): in three real nights about 3000 of 6000 were
the same each night. So a pixel is corrected only if it was hot in two nights in a row;
the first night's map is used alone until there is a second. The map is rebuilt every
night (hot pixels grow with temperature and age) and kept on disk, so it is used right
after a restart. Pixels on the map are replaced by the median
of their neighbourhood in every stored frame. The map is built from the raw frames; a
map built from corrected frames would lose the pixels it corrects.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

log = logging.getLogger(__name__)

Gray = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class HotPixelConfig:
    max_sun_deg: float = -12.0  # only dark frames: a bright sky hides nothing
    sample_s: float = 600.0  # at most one frame per 10 min goes into the minimum
    min_frames: int = 12
    min_span_s: float = 7200.0  # stars move about 30° in 2 h
    threshold: int = 30  # brighter than the 21x21 median of the minimum by this much
    max_spot_px: int = 4  # larger spots are not a pixel defect (a lamp, a planet's track)
    max_pixels: int = 20_000  # more: something went wrong (a static light, a covered lens)


class HotPixels:
    def __init__(self, path: Path, cfg: HotPixelConfig | None = None) -> None:
        self._path = path
        self._cfg = cfg or HotPixelConfig()
        self._night: str | None = None
        self._min: Gray | None = None
        self._count = 0
        self._first: float | None = None
        self._last: float | None = None
        self._built_for: str | None = None
        self._ys: npt.NDArray[np.int64] = np.zeros(0, dtype=np.int64)
        self._xs: npt.NDArray[np.int64] = np.zeros(0, dtype=np.int64)
        self._shape: tuple[int, int] | None = None
        # The last night's raw spots: tonight's map keeps only what was in them as well.
        self._prev: set[tuple[int, int]] = set()
        self._load()

    @property
    def count(self) -> int:
        return int(self._ys.size)

    def _load(self) -> None:
        try:
            with np.load(self._path) as z:
                ys = np.asarray(z["ys"], dtype=np.int64)
                xs = np.asarray(z["xs"], dtype=np.int64)
                shape = np.asarray(z["shape"], dtype=np.int64)
                cys = np.asarray(z["cand_ys"], dtype=np.int64)
                cxs = np.asarray(z["cand_xs"], dtype=np.int64)
        except (OSError, KeyError, ValueError):
            return
        self._ys, self._xs = ys, xs
        self._shape = (int(shape[0]), int(shape[1]))
        self._prev = set(zip(cys.tolist(), cxs.tolist(), strict=True))
        log.info("hot pixel map: %d pixels", self.count)

    def _save(self) -> None:
        if self._shape is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.stem + ".tmp.npz")
        cand = sorted(self._prev)
        np.savez(
            tmp,
            ys=self._ys,
            xs=self._xs,
            shape=np.array(self._shape, dtype=np.int64),
            cand_ys=np.array([c[0] for c in cand], dtype=np.int64),
            cand_xs=np.array([c[1] for c in cand], dtype=np.int64),
        )
        tmp.replace(self._path)

    def observe(
        self, at: datetime, night_id: str, sun_elevation: float, image: npt.NDArray[np.uint8]
    ) -> None:
        """Feed a raw stored frame; rebuilds the map once a night has enough frames."""
        cfg = self._cfg
        if sun_elevation > cfg.max_sun_deg:
            return
        t = at.timestamp()
        if night_id != self._night:
            self._night, self._min, self._count, self._first, self._last = (
                night_id, None, 0, None, None,
            )  # fmt: skip
        if self._last is not None and t - self._last < cfg.sample_s:
            return
        gray: Gray = (
            np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
            if image.ndim == 3
            else image
        )
        if self._min is not None and self._min.shape != gray.shape:
            self._min, self._count, self._first = None, 0, None
        self._min = gray.copy() if self._min is None else np.minimum(self._min, gray)
        self._count += 1
        self._first = t if self._first is None else self._first
        self._last = t
        if (
            self._built_for != night_id
            and self._count >= cfg.min_frames
            and t - self._first >= cfg.min_span_s
        ):
            self._build(self._min, night_id)

    def _build(self, minimum: Gray, night_id: str) -> None:
        cfg = self._cfg
        self._built_for = night_id
        # A wide median: a lamp or a lit roof stays one large spot (rejected by size)
        # instead of leaving its corners as small ones.
        diff = minimum.astype(np.int16) - cv2.medianBlur(minimum, 21).astype(np.int16)
        spots = (diff > cfg.threshold).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(spots, connectivity=8)
        small = np.zeros(int(n), dtype=np.bool_)
        small[1:] = stats[1:, cv2.CC_STAT_AREA] <= cfg.max_spot_px
        lab = np.asarray(labels, dtype=np.int64)
        ys, xs = np.nonzero(small[lab])
        if ys.size > cfg.max_pixels:
            log.warning("hot pixel map for %s: %d pixels, not used", night_id, ys.size)
            return
        shape = (int(minimum.shape[0]), int(minimum.shape[1]))
        tonight = set(zip(ys.tolist(), xs.tolist(), strict=True))
        keep = tonight & self._prev if self._prev and shape == self._shape else tonight
        ordered = sorted(keep)
        self._ys = np.array([p[0] for p in ordered], dtype=np.int64)
        self._xs = np.array([p[1] for p in ordered], dtype=np.int64)
        self._shape = shape
        self._prev = tonight
        self._save()
        log.info("hot pixel map for night %s: %d pixels", night_id, self.count)

    def apply(self, image: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
        """A copy with the mapped pixels (and their colour fringe) replaced by the median
        of the 5x5 neighbourhood; the image itself if there is nothing to do."""
        shape = self._shape
        if self._ys.size == 0 or shape is None or shape != (image.shape[0], image.shape[1]):
            return image
        h, w = shape
        # Debayering spreads a hot pixel into its neighbours: correct the 3x3 around it.
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[self._ys, self._xs] = 1
        ys, xs = np.nonzero(cv2.dilate(mask, np.ones((3, 3), np.uint8)))
        out = image.copy()
        offsets = [(dy, dx) for dy in range(-2, 3) for dx in range(-2, 3)]
        samples = [
            image[np.minimum(np.maximum(ys + dy, 0), h - 1),
                  np.minimum(np.maximum(xs + dx, 0), w - 1)]
            for dy, dx in offsets
        ]  # fmt: skip
        out[ys, xs] = np.median(np.asarray(samples), axis=0).astype(np.uint8)
        return out
