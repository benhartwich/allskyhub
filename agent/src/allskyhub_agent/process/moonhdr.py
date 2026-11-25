"""Moon HDR (SPEC §4.13, setting `moon_hdr`, default off).

Near full moon the long night frame burns out the moon and a wide halo around it. When
the previous frame showed such a patch, a fixed ladder of short exposures is taken right
after the long one and fused into the stored picture, only in the moon's region (Mertens
exposure fusion). Measurements and detections keep using the long frame.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_agent.adapters.camera import Image

# SPEC §4.13: a pixel is saturated when any channel reaches this.
SATURATED = 245
# ... and the patch counts from this share of the sky mask.
MIN_PATCH = 0.0005
# The ladder: 1 ms holds the disc, 500 ms the halo, the steps between the rest.
SHORT_US = (1000, 10_000, 100_000, 500_000)
GROW_PX = 40  # the region: the patch grown by this ...
FEATHER_PX = 25.0  # ... and feathered (Gaussian sigma)
PAD_PX = 80  # the fusion box around the grown patch
# Mertens weights: contrast, saturation, well-exposedness.
WEIGHTS = (1.0, 0.0, 3.0)

Point = tuple[float, float]
Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class Fused:
    image: Image
    region_px: int  # saturated pixels of the moon's patch


def moon_patch(long: Image, at: Point | None) -> npt.NDArray[np.bool_] | None:
    """The saturated patch that holds the point `at` (the moon), or the largest one when
    `at` is None; None when there is none there. Neighbours within 4 px join a patch."""
    sat = (long.max(axis=2) >= SATURATED).astype(np.uint8)
    if not sat.any():
        return None
    joined = cv2.dilate(sat, np.ones((9, 9), np.uint8))
    count, found = cv2.connectedComponents(joined)
    labels: npt.NDArray[np.int32] = np.asarray(found, dtype=np.int32)
    if count < 2:
        return None
    if at is not None:
        x, y = round(at[0]), round(at[1])
        h, w = sat.shape
        if not (0 <= x < w and 0 <= y < h):
            return None
        label = int(labels[y, x])
        if label == 0:
            return None
    else:
        sizes = np.bincount(labels[sat > 0], minlength=count)
        sizes[0] = 0
        label = int(np.argmax(sizes))
    return (labels == label) & (sat > 0)


def wanted(long: Image, sky: Mask | None, at: Point | None) -> bool:
    """SPEC §4.13 "When": the moon's saturated patch is at least 0.05 % of the sky mask."""
    patch = moon_patch(long, at)
    if patch is None:
        return False
    area = int(sky.sum()) if sky is not None else long.shape[0] * long.shape[1]
    return int(patch.sum()) >= MIN_PATCH * area


def brightest(image: Image) -> Point:
    """The brightest pixel (the moon in a 1 ms frame), as (x, y)."""
    peak = image.max(axis=2)
    y, x = np.unravel_index(int(np.argmax(peak)), peak.shape)
    return float(x), float(y)


def fuse(long: Image, shorts: Sequence[Image], at: Point | None) -> Fused | None:
    """The long frame with the moon's region fused from it and the short frames (longest
    first). `at` is the moon's predicted pixel; without it the brightest point of the
    shortest frame. None when the long frame has no saturated patch there."""
    patch = moon_patch(long, at if at is not None else brightest(shorts[-1]))
    if patch is None:
        return None
    h, w = patch.shape
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * GROW_PX + 1, 2 * GROW_PX + 1))
    grown = cv2.dilate(patch.astype(np.uint8), disc)
    ys, xs = np.nonzero(grown)
    y0, y1 = max(int(ys.min()) - PAD_PX, 0), min(int(ys.max()) + PAD_PX + 1, h)
    x0, x1 = max(int(xs.min()) - PAD_PX, 0), min(int(xs.max()) + PAD_PX + 1, w)
    box = grown[y0:y1, x0:x1].astype(np.float32)
    region = np.minimum(cv2.GaussianBlur(box, (0, 0), FEATHER_PX) * 1.5, 1.0)[..., None]
    frames = [np.ascontiguousarray(f[y0:y1, x0:x1]) for f in (long, *shorts)]
    mertens = cv2.createMergeMertens(*WEIGHTS)
    merged: npt.NDArray[np.float32] = np.asarray(mertens.process(frames), dtype=np.float32)
    fused = np.minimum(np.maximum(merged * 255.0, 0.0), 255.0)
    near = long[y0:y1, x0:x1].astype(np.float32)
    # SPEC §4.13: matched to the long frame at the region's edge by a power curve.
    band = (region[..., 0] > 0.05) & (region[..., 0] < 0.6) & (near.max(axis=2) < 240)
    if int(band.sum()) > 100:
        f = min(max(float(np.median(fused[band])) / 255.0, 1e-3), 0.999)
        lo = min(max(float(np.median(near[band])) / 255.0, 1e-3), 0.999)
        fused = 255.0 * np.power(fused / 255.0, np.log(lo) / np.log(f))
    out = long.copy()
    blended = region * fused + (1.0 - region) * near + 0.5
    out[y0:y1, x0:x1] = np.minimum(np.maximum(blended, 0.0), 255.0).astype(np.uint8)
    return Fused(out, int(patch.sum()))
