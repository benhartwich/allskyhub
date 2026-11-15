"""White balance at night from the sky itself (SPEC §4.11).

A camera's own white balance is no help at night: ZWO has none worth using, and
libcamera's AWB follows the noise. A clear night sky away from the horizon is close to
grey, so the agent nudges the camera's red and blue gains until the sky's median red
and blue match its green. Slowly (an exponential mean over 1 hour, measured every 10
minutes, at most 5 % a step) and within ±40 % of the camera's normal values, so one odd
frame or a slow change of the sky cannot run away with the colours.

It does not learn while the sky is not grey on purpose: a bright moon (blue scattered
light), clouds (they reflect street lights) and an aurora (a green sky stays green).
The gains are factors on the camera's normal values: 1.0 is the owner's or the camera's
own white balance, which is also used by day. They are kept on disk, so a restart
starts with the last night's colours.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from allskyhub_agent.adapters.camera import Image

log = logging.getLogger(__name__)

Mask = npt.NDArray[np.bool_]


@runtime_checkable
class WhiteBalanceCamera(Protocol):
    """A camera whose white balance the agent can set (ZWO)."""

    def set_white_balance(self, red: float, blue: float) -> None:
        """Red and blue gains as factors on the camera's normal values."""
        ...


@dataclass(frozen=True)
class WhiteBalanceConfig:
    every: timedelta = timedelta(minutes=10)
    time_constant: timedelta = timedelta(hours=1)
    max_step: float = 0.05  # at most 5 % a step
    limit: float = 0.40  # within ±40 % of the normal values
    min_pixels: int = 500
    min_green: float = 4.0  # darker skies give no usable ratio in 8 bit
    clipped: int = 250
    star_percentile: float = 95.0  # brighter pixels are stars or lights


@dataclass(frozen=True)
class Gains:
    red: float = 1.0
    blue: float = 1.0


def sky_ratios(image: Image, sky: Mask, cfg: WhiteBalanceConfig) -> tuple[float, float] | None:
    """Median red/green and blue/green of the sky pixels without stars and clipped
    pixels; None when there are too few or the sky is too dark."""
    px = image[sky].astype(np.float32)
    if px.shape[0] < cfg.min_pixels:
        return None
    keep = px.max(axis=1) < cfg.clipped
    px = px[keep]
    if px.shape[0] < cfg.min_pixels:
        return None
    green = px[:, 1]
    # The star cut without np.percentile or np.sort (partly unknown to pyright, numpy 2.5).
    ordered = green.copy()
    ordered.sort()
    cut = float(ordered[min(ordered.size - 1, int(ordered.size * cfg.star_percentile / 100))])
    px = px[green <= cut]
    r, g, b = (float(np.median(px[:, i])) for i in range(3))
    if g < cfg.min_green or r <= 0 or b <= 0:
        return None
    return r / g, b / g


def inner_sky(mask: Mask, frac: float) -> Mask:
    """The part of the mask within `frac` of the image circle's radius around the centre:
    with a fisheye 0.78 of the radius is about 20° above the horizon."""
    h, w = mask.shape
    yy, xx = np.ogrid[0:h, 0:w]
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return mask
    cy, cx = (h - 1) / 2, (w - 1) / 2
    radius = float(np.sqrt(((ys - cy) ** 2 + (xs - cx) ** 2).max()))
    disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= (radius * frac) ** 2
    return mask & disc


class NightWhiteBalance:
    def __init__(self, path: Path, cfg: WhiteBalanceConfig | None = None) -> None:
        self._path = path
        self._cfg = cfg or WhiteBalanceConfig()
        self._gains = Gains()
        self._last: datetime | None = None
        self._load()

    @property
    def gains(self) -> Gains:
        """The gains for night frames."""
        return self._gains

    def due(self, at: datetime) -> bool:
        return self._last is None or at - self._last >= self._cfg.every

    def observe(self, at: datetime, image: Image, sky: Mask, hold: bool) -> Gains:
        """Learn from a good night frame taken with the current gains, every 10 minutes."""
        if not self.due(at):
            return self._gains
        self._last = at
        if hold:
            return self._gains
        ratios = sky_ratios(image, sky, self._cfg)
        if ratios is None:
            return self._gains
        cfg = self._cfg
        alpha = 1.0 - math.exp(-cfg.every / cfg.time_constant)
        lo, hi = 1.0 - cfg.limit, 1.0 + cfg.limit

        def toward_grey(gain: float, ratio: float) -> float:
            # Log space: the mean of 1 h moves the gain by a share of the way to grey.
            step = math.exp(-alpha * math.log(ratio))
            step = min(1.0 + cfg.max_step, max(1.0 - cfg.max_step, step))
            return min(hi, max(lo, gain * step))

        g = self._gains
        red, blue = toward_grey(g.red, ratios[0]), toward_grey(g.blue, ratios[1])
        new = Gains(round(red, 4), round(blue, 4))
        if new != g:
            log.info("night white balance: red %.3f, blue %.3f", new.red, new.blue)
            self._gains = new
            self._save()
        return new

    def _load(self) -> None:
        try:
            d = json.loads(self._path.read_text())
            red, blue = float(d["red"]), float(d["blue"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        lo, hi = 1.0 - self._cfg.limit, 1.0 + self._cfg.limit
        if lo <= red <= hi and lo <= blue <= hi:
            self._gains = Gains(red, blue)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"red": self._gains.red, "blue": self._gains.blue}) + "\n")
            tmp.replace(self._path)
        except OSError as exc:
            log.warning("night white balance not saved: %s", exc)
