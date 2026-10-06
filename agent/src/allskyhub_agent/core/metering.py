"""Brightness measurement inside the sky mask (SPEC §4.3)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Mask = npt.NDArray[np.bool_]


def circle_mask(height: int, width: int, radius_frac: float = 0.48) -> Mask:
    """Circular image-circle mask centred in the frame; radius relative to the short side."""
    yy, xx = np.mgrid[0:height, 0:width]
    r = min(width, height) * radius_frac
    return (xx - width / 2) ** 2 + (yy - height / 2) ** 2 <= r * r


def mean_brightness(image: npt.NDArray[np.uint8], mask: Mask | None = None) -> float:
    """Mean brightness 0..1 of an 8-bit image (mono or RGB), only inside `mask`."""
    lum = image.mean(axis=2) if image.ndim == 3 else image.astype(np.float64)
    values = lum[mask] if mask is not None else lum
    if values.size == 0:
        return 0.0
    return float(values.mean() / 255.0)
