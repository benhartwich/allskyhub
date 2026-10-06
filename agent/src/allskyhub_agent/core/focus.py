"""Focus measure for the focus helper (SPEC §7).

Variance of the Laplacian on the luminance of a central crop: sharper images have more
fine detail and score higher. Only relative values matter, while the scene and its
brightness stay roughly the same.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


def sharpness(image: npt.NDArray[np.uint8], crop_frac: float = 0.25) -> float:
    """Focus score of an 8-bit image (mono or RGB); higher is sharper.

    `crop_frac` is the size of the central square crop relative to the short side; the
    centre of a fisheye image holds the zenith, where focus matters most.
    """
    lum = image.mean(axis=2) if image.ndim == 3 else image.astype(np.float64)
    h, w = lum.shape
    side = max(3, int(min(h, w) * crop_frac))
    y0, x0 = (h - side) // 2, (w - side) // 2
    c = lum[y0 : y0 + side, x0 : x0 + side]
    lap = c[:-2, 1:-1] + c[2:, 1:-1] + c[1:-1, :-2] + c[1:-1, 2:] - 4.0 * c[1:-1, 1:-1]
    return float(lap.var())
