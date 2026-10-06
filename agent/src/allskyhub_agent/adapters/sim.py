"""Simulated all-sky camera.

Renders a fisheye disc whose brightness follows a sky radiance (mean signal per second
at unit gain), so auto exposure and day/night switching behave like on real hardware.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from allskyhub_agent.adapters.camera import CaptureRequest, Frame, Image
from allskyhub_agent.core.clock import Clock


def sky_radiance(sun_elevation: float) -> float:
    """Rough sky radiance for a sun elevation: about 1000/s by day, 0.001/s at night.

    Log-linear between sunset (0°) and astronomical night (-18°).
    """
    lo, hi = -18.0, 0.0
    e = max(lo, min(hi, sun_elevation))
    log_r = -3.0 + (e - lo) / (hi - lo) * 6.0
    return float(10**log_r)


class SimCamera:
    """`Camera` implementation without hardware."""

    def __init__(
        self,
        radiance: Callable[[], float],
        width: int = 640,
        height: int = 360,
        gain_db_per_unit: float = 0.1,
        seed: int = 1,
        clock: Clock | None = None,
        readout_s: float = 1.0,
    ) -> None:
        self._radiance = radiance
        self._clock = clock
        self._readout_s = readout_s
        self._w = width
        self._h = height
        self._gain_db = gain_db_per_unit
        self._rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:height, 0:width]
        r = min(width, height) * 0.48
        self._disc = (xx - width / 2) ** 2 + (yy - height / 2) ** 2 <= r * r
        n = 60
        self._stars = (
            self._rng.integers(0, height, n),
            self._rng.integers(0, width, n),
            self._rng.uniform(0.002, 0.05, n),
        )

    @property
    def name(self) -> str:
        return "sim"

    def capture(self, req: CaptureRequest) -> Frame:
        gain = 10 ** (req.gain * self._gain_db / 20.0)
        scale = req.exposure_us / 1e6 * gain
        level = self._radiance() * scale
        img = np.full((self._h, self._w), level, dtype=np.float32)
        ys, xs, flux = self._stars
        img[ys, xs] += (flux * scale).astype(np.float32)
        img += self._rng.normal(0.0, 0.004, img.shape).astype(np.float32)
        img[~self._disc] = 0.0
        # ufuncs instead of clip()/stack(): their numpy 2.5 stubs are partially unknown to pyright.
        mono = np.minimum(np.maximum(img * 255.0, 0.0), 255.0).astype(np.uint8)
        rgb: Image = np.empty((self._h, self._w, 3), dtype=np.uint8)
        rgb[:, :, :] = mono[:, :, None]
        if self._clock is not None:
            # A real camera blocks for exposure and readout; let simulated time pass the same way.
            self._clock.sleep(req.exposure_us / 1e6 + self._readout_s)
        return Frame(image=rgb, exposure_us=req.exposure_us, gain=req.gain, sensor_temp_c=20.0)

    def close(self) -> None:
        return None
