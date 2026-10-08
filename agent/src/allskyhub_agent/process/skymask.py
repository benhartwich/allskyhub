"""Sky mask learned from the night's frames (SPEC §4.7).

At night the sky glows (airglow, light pollution, moonlight on clouds) and everything in
front of it is a dark silhouette: trees, roofs, the black corners outside the lens. In
the median of a night's frames stars and passing clouds are gone and that contrast is
left; Otsu's threshold splits it, and the largest bright region is the sky. On
Benjamin's camera it outlines the trees and the real image circle in a clear and in a
cloudy night alike, and leaves out the overlay text in the corners.

The mask is relearned every night and kept on disk. Until there is one, everything uses
the profile's image circle as before.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

log = logging.getLogger(__name__)

Gray = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]

WORK_WIDTH = 960  # the mask is learned and stored at this width


@dataclass(frozen=True)
class SkyMaskConfig:
    max_sun_deg: float = -12.0
    sample_s: float = 600.0
    min_frames: int = 12
    min_span_s: float = 7200.0
    max_frames: int = 60  # a long winter night: keep every other sample beyond this
    min_sky: float = 0.2  # a mask covering less or more of the frame is not believed
    max_sky: float = 0.97


class SkyMask:
    def __init__(self, path: Path, cfg: SkyMaskConfig | None = None) -> None:
        self._path = path
        self._cfg = cfg or SkyMaskConfig()
        self._lock = threading.Lock()
        self._mask: Mask | None = None  # at WORK_WIDTH
        self._scaled: dict[tuple[int, int], Mask] = {}
        self.version = 0  # incremented whenever the mask changes
        self._night: str | None = None
        self._frames: list[Gray] = []
        self._first: float | None = None
        self._last: float | None = None
        self._built_for: str | None = None
        self._load()

    def _load(self) -> None:
        img = cv2.imread(str(self._path), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            self._set(np.asarray(img, dtype=np.uint8) > 127)

    def _set(self, mask: Mask) -> None:
        with self._lock:
            self._mask = mask
            self._scaled = {}
            self.version += 1

    def get(self, height: int, width: int) -> Mask | None:
        """The mask for a frame of this size, None until one was learned."""
        with self._lock:
            if self._mask is None:
                return None
            m = self._scaled.get((height, width))
            if m is None:
                small = self._mask.astype(np.uint8) * 255
                big = cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)
                m = np.asarray(big, dtype=np.uint8) > 127
                self._scaled[(height, width)] = m
            return m

    def observe(
        self, at: datetime, night_id: str, sun_elevation: float, image: npt.NDArray[np.uint8]
    ) -> None:
        cfg = self._cfg
        if sun_elevation > cfg.max_sun_deg:
            return
        t = at.timestamp()
        if night_id != self._night:
            self._night, self._frames, self._first, self._last = night_id, [], None, None
        if self._last is not None and t - self._last < cfg.sample_s:
            return
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
        h, w = gray.shape[:2]
        size = (WORK_WIDTH, max(1, round(h * WORK_WIDTH / w)))
        small = np.asarray(cv2.resize(gray, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)
        if self._frames and self._frames[0].shape != small.shape:
            self._frames, self._first = [], None
        self._frames.append(small)
        if len(self._frames) > cfg.max_frames:
            self._frames = self._frames[::2]
        self._first = t if self._first is None else self._first
        self._last = t
        if (
            self._built_for != night_id
            and len(self._frames) >= cfg.min_frames
            and t - self._first >= cfg.min_span_s
        ):
            self._built_for = night_id
            self._build(night_id)

    def _build(self, night_id: str) -> None:
        cfg = self._cfg
        med = np.asarray(np.median(np.asarray(self._frames), axis=0), dtype=np.uint8)
        med = np.asarray(cv2.GaussianBlur(med, (5, 5), 0), dtype=np.uint8)
        _, bw = cv2.threshold(med, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
        if int(n) < 2:
            return
        sky = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        lab = np.asarray(labels, dtype=np.int32)
        mask = (lab == sky).astype(np.uint8)
        # Fill holes: a dark patch inside the sky is still sky.
        inv = (1 - mask).astype(np.uint8)
        _, hl, hs, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
        hlab = np.asarray(hl, dtype=np.int32)
        h, w = mask.shape
        for i in range(1, int(hs.shape[0])):
            x, y = int(hs[i, cv2.CC_STAT_LEFT]), int(hs[i, cv2.CC_STAT_TOP])
            bw_, bh = int(hs[i, cv2.CC_STAT_WIDTH]), int(hs[i, cv2.CC_STAT_HEIGHT])
            if x > 0 and y > 0 and x + bw_ < w and y + bh < h:
                mask[hlab == i] = 1
        share = float(mask.mean())
        if not cfg.min_sky <= share <= cfg.max_sky:
            log.warning("sky mask for %s covers %.0f %% of the frame, not used", night_id,
                        100 * share)  # fmt: skip
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.stem + ".tmp.png")
        cv2.imwrite(str(tmp), mask * 255)
        tmp.replace(self._path)
        self._set(mask > 0)
        log.info("sky mask for night %s: %.0f %% of the frame", night_id, 100 * share)
