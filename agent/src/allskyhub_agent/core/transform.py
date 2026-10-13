"""Flip and rotation of every frame right after capture (SPEC §4.1)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

U8 = npt.NDArray[np.uint8]


def turn(image: U8, flip: str = "none", rotate: int = 0) -> U8:
    """`flip` none/horizontal/vertical first, then `rotate` 0/90/180/270 clockwise."""
    out = image
    if flip == "horizontal":
        out = out[:, ::-1]
    elif flip == "vertical":
        out = out[::-1]
    r = rotate % 360
    if r == 90:
        out = out.swapaxes(0, 1)[:, ::-1]
    elif r == 180:
        out = out[::-1, ::-1]
    elif r == 270:
        out = out.swapaxes(0, 1)[::-1]
    return out if out is image else np.ascontiguousarray(out)
