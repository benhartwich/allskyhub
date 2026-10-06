"""Camera interface (architecture rule 2).

Real adapters (ZWO ASI SDK, libcamera) follow in M1; `SimCamera` lets everything run on a
laptop and makes the capture loop testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

Image = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class CaptureRequest:
    exposure_us: int
    gain: float


@dataclass(frozen=True)
class Frame:
    image: Image  # H x W x 3, RGB, 8 bit
    exposure_us: int
    gain: float
    sensor_temp_c: float | None = None


class Camera(Protocol):
    """A camera that takes one frame at a time."""

    @property
    def name(self) -> str: ...

    def capture(self, req: CaptureRequest) -> Frame: ...

    def close(self) -> None: ...
