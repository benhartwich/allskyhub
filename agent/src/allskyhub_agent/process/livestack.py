"""A calmer live image from the last few frames (SPEC §4.12).

At dusk and dawn the exposures are short and the frames noisy; averaging the newest
frames calms the live image without star alignment. Stars move about 0.25° a minute,
so the frames in the mean span at most 15 s from the start of the oldest to the end of
the newest, and there are at most 8 of them. Long night exposures and frames far apart
(the day delay) therefore stay single. Only the live image uses the mean: stored
frames, detections, products and the sky values stay single frames, so a meteor never
smears.

The frames are kept at the live image's width, which keeps 8 of them at a few MB.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from PIL import Image as PILImage

from allskyhub_agent.adapters.camera import Image

SPAN = timedelta(seconds=15)
MAX_FRAMES = 8


@dataclass(frozen=True)
class _Frame:
    start: datetime
    end: datetime
    image: Image


class LiveStack:
    def __init__(self, width: int, span: timedelta = SPAN, max_frames: int = MAX_FRAMES) -> None:
        self._width = width
        self._span = span
        self._frames: deque[_Frame] = deque(maxlen=max_frames)

    def add(self, image: Image, start: datetime, exposure_us: int) -> Image:
        """The mean of this frame and the newest earlier ones within the span, at the
        live width; the frame alone when it is the only one."""
        small = _scaled(image, self._width)
        end = start + timedelta(microseconds=exposure_us)
        frames = self._frames
        if frames and frames[-1].image.shape != small.shape:
            frames.clear()
        frames.append(_Frame(start, end, small))
        while len(frames) > 1 and frames[0].start < end - self._span:
            frames.popleft()
        if len(frames) == 1:
            return small
        total = np.zeros(small.shape, dtype=np.uint32)
        for f in frames:
            total += f.image
        mean = (total + len(frames) // 2) // len(frames)
        return mean.astype(np.uint8)

    def clear(self) -> None:
        self._frames.clear()

    @property
    def size(self) -> int:
        return len(self._frames)


def _scaled(image: Image, width: int) -> Image:
    h, w = image.shape[:2]
    if w <= width:
        return image
    pil = PILImage.fromarray(image).resize((width, max(1, round(h * width / w))))
    return np.asarray(pil, dtype=np.uint8)
