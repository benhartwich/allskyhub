"""Star size as half-flux radius (HFR) for focusing and the sky values (SPEC §6.3, §7).

The HFR of a star is the mean distance of its light from its centre, flux weighted, in
pixels: a sharp star has a small HFR, a blurred or defocused one a large one. Unlike a
contrast score it hardly depends on the scene or its brightness, so values of different
nights and cameras with the same sensor compare. The idea comes from Zenivo
(github.com/hoanb1/zenivo, MIT); this is an own, simpler implementation.

Stars are found on the white top-hat (the frame minus its local background): local
maxima clearly above the noise. Each is measured on a stamp around it after subtracting
the stamp's border median. Saturated stars (flat tops), stars close to others and
single hot pixels are skipped; the value is the median of the brightest usable stars.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

Gray = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class HfrConfig:
    radius: int = 16  # stamp half side, px: larger stars are not measured
    background_px: int = 15  # top-hat kernel, removes everything larger
    k_sigma: float = 5.0  # a star's peak above the noise
    min_peak: float = 4.0  # ... and at least this many DN
    saturated: int = 250  # flat tops cannot be measured
    max_stars: int = 25
    min_stars: int = 3


@dataclass(frozen=True)
class Hfr:
    hfr: float  # median HFR of the measured stars, px
    stars: int  # how many were measured


def _noise(gray: Gray) -> float:
    """Pixel noise from horizontal neighbour differences (robust, MAD)."""
    d = gray[:, 1:].astype(np.int16) - gray[:, :-1].astype(np.int16)
    mad = float(np.median(np.abs(d - np.median(d))))
    return max(0.75, 1.4826 * mad / math.sqrt(2.0))


def star_hfr(gray: Gray, cfg: HfrConfig | None = None) -> Hfr | None:
    """HFR of the stars in a gray image (a crop of the frame), or None with too few."""
    cfg = cfg or HfrConfig()
    h, w = gray.shape
    r = cfg.radius
    if h <= 2 * r + 2 or w <= 2 * r + 2:
        return None
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.background_px, cfg.background_px))
    resid = np.asarray(cv2.subtract(gray, cv2.morphologyEx(gray, cv2.MORPH_OPEN, k)), np.uint8)
    noise = _noise(gray)
    smooth = np.asarray(cv2.GaussianBlur(resid, (3, 3), 0), np.uint8)
    peaks = np.asarray(cv2.dilate(smooth, np.ones((7, 7), np.uint8)), np.uint8)
    thr = max(cfg.k_sigma * noise, cfg.min_peak)
    ys, xs = np.nonzero((smooth == peaks) & (smooth > thr))
    inside = (xs >= r) & (ys >= r) & (xs < w - r) & (ys < h - r)
    ys, xs = ys[inside], xs[inside]
    order = np.argsort(-smooth[ys, xs].astype(np.int16), kind="stable")
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1].astype(np.float32)
    taken: list[tuple[int, int]] = []
    values: list[float] = []
    for i in order[: cfg.max_stars * 8]:
        x, y = int(xs[i]), int(ys[i])
        # A neighbour within the stamp would add its light to this star's radius.
        if any(abs(x - tx) <= r and abs(y - ty) <= r for tx, ty in taken):
            continue
        taken.append((x, y))
        stamp = gray[y - r : y + r + 1, x - r : x + r + 1].astype(np.float32)
        if float(stamp.max()) >= cfg.saturated:
            continue
        ring = np.concatenate([stamp[0], stamp[-1], stamp[1:-1, 0], stamp[1:-1, -1]])
        bg = float(np.median(ring))
        s = stamp - bg
        lit = s > 2.0 * noise
        if int(lit.sum()) < 3:  # a single hot or cosmic pixel
            continue
        # Only the star itself: the pixels above the noise that hang together with its
        # peak, plus one pixel around, so noise farther out does not make it larger.
        grown = np.asarray(cv2.GaussianBlur(s, (3, 3), 0), np.float32) > 1.35 * noise
        n, labels = cv2.connectedComponents(grown.astype(np.uint8), connectivity=8)
        own = int(labels[r, r])
        if n < 2 or own == 0:
            continue
        blob = (labels == own).astype(np.uint8)
        if blob[0].any() or blob[-1].any() or blob[:, 0].any() or blob[:, -1].any():
            continue  # larger than the stamp, or running into a neighbour's light
        keep = np.asarray(cv2.dilate(blob, np.ones((3, 3), np.uint8)), np.uint8) > 0
        sv = np.where(keep & (s > 0), s, 0.0)
        flux = float(sv.sum())
        if flux <= 0:
            continue
        cx, cy = float((sv * xx).sum()) / flux, float((sv * yy).sum()) / flux
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        values.append(float((dist * sv).sum()) / flux)
        if len(values) >= cfg.max_stars:
            break
    if len(values) < cfg.min_stars:
        return None
    return Hfr(round(float(np.median(values)), 2), len(values))


def gray_of(image: npt.NDArray[np.uint8]) -> Gray:
    if image.ndim == 3:
        return np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
    return image


def centre_crop(gray: Gray, frac: float) -> Gray:
    """The central square, `frac` of the short side: the zenith of a fisheye."""
    h, w = gray.shape
    side = max(1, int(min(h, w) * frac))
    y0, x0 = (h - side) // 2, (w - side) // 2
    return gray[y0 : y0 + side, x0 : x0 + side]


def edge_crops(gray: Gray, frac: float, at: float) -> list[Gray]:
    """Four squares of `frac` of the short side toward the image circle's edge, `at` of
    its radius from the centre (above, right, below, left)."""
    h, w = gray.shape
    side = max(1, int(min(h, w) * frac))
    reach = at * min(h, w) / 2
    out: list[Gray] = []
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        cx, cy = w / 2 + dx * reach, h / 2 + dy * reach
        x0 = min(max(0, int(cx - side / 2)), w - side)
        y0 = min(max(0, int(cy - side / 2)), h - side)
        out.append(gray[y0 : y0 + side, x0 : x0 + side])
    return out


@dataclass(frozen=True)
class FocusHfr:
    centre: Hfr | None
    edge: Hfr | None  # the median of the four edge crops that had stars


def focus_hfr(image: npt.NDArray[np.uint8], cfg: HfrConfig | None = None) -> FocusHfr:
    """HFR in the centre and toward the edge, for the focus helper (SPEC §7)."""
    gray = gray_of(image)
    centre = star_hfr(centre_crop(gray, 0.25), cfg)
    sides = [v for v in (star_hfr(c, cfg) for c in edge_crops(gray, 0.2, 0.62)) if v]
    edge = None
    if sides:
        edge = Hfr(round(float(np.median([v.hfr for v in sides])), 2), sum(v.stars for v in sides))
    return FocusHfr(centre, edge)
