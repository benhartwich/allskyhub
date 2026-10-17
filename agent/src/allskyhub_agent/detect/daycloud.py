"""Cloud cover by day from the sky's colour saturation (SPEC §6.3 `cloud_cover`, roadmap L22).

A clear sky is a saturated blue, cloud is grey or white. A fixed blue-to-red threshold
fails on cameras without an IR cut filter (an overcast sky stays bluish on the ASI678MC),
but every camera's own clear and overcast saturations differ clearly, so the threshold T
is learned from the camera's own day frames.

Per frame (sun ≥ 3°, only with an orientation, SPEC §4.8):
1. The frame is scaled to 960 px width.
2. The used pixels are inside the sky mask (SPEC §4.7), at least 15° above the horizon
   and more than 25° from the sun.
3. HSV saturation (max - min)/max per pixel; null when more than 20 % of the used pixels
   are clipped (any channel ≥ 250): glare.
4. The frame's value is the share of the used pixels with a saturation below T.

T = (5th + 95th percentile of the frame medians of that saturation over the day frames of
the last 30 days) / 2 once there are at least 10 days, else 0.35. The medians are kept as
a small histogram per solar day in `<data>/calibration/day_saturation.json`.

The reported value is the median of the day values of the last 15 minutes.

Cost on the Pi: the pixel set (mask, altitude and the unit vector of every used pixel)
is computed once per frame size, orientation and mask, the sun's exclusion again only
after the sun has moved 0.5°. Per frame there remain the scaling, max and min of the used
pixels and one histogram over the (max, min) pairs, from which the median, the share
below T and nothing else are read.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import json
import logging
import math
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_agent.calib.solve import Solution
from allskyhub_protocol import FrameInfo
from allskyhub_sky import alt_az, sidereal_deg
from allskyhub_sky.astro import sun_radec

log = logging.getLogger(__name__)

Mask = npt.NDArray[np.bool_]
CALIBRATION_FILE = "day_saturation.json"
_SUN_STEP_DEG = 0.5  # the used pixels follow the sun in steps of this
_SUN_STEP_COS = math.cos(math.radians(_SUN_STEP_DEG))


@dataclass(frozen=True)
class DayCloudConfig:
    """The numbers of SPEC §6.3 `cloud_cover` by day."""

    width: int = 960  # the frame is scaled to this width
    min_alt: float = 15.0  # degrees above the horizon
    sun_deg: float = 25.0  # pixels within this distance of the sun are left out
    clip_level: int = 250  # a pixel with any channel at or above this is clipped
    clip_max: float = 0.20  # null with more clipped pixels than this share
    default_threshold: float = 0.35  # T until the camera has its own
    min_days: int = 10  # days with day frames before T is the camera's own
    history_days: int = 30  # days the frame medians are kept for T
    window: timedelta = timedelta(minutes=15)  # the reported value is the median over this
    bins: int = 200  # histogram bins of the frame medians over 0..1
    save_every: timedelta = timedelta(minutes=10)  # frame time between writes of the file


@dataclass(frozen=True)
class _Geometry:
    """The pixels of the scaled frame that can be used, and where they look."""

    key: tuple[int, int, Solution, int]
    size: tuple[int, int]  # (w, h) of the scaled frame
    index: npt.NDArray[np.intp]  # flat indices into the scaled frame
    vec: npt.NDArray[np.float32]  # (n, 3) unit vectors (east, north, up) of those pixels


def _geometry(
    key: tuple[int, int, Solution, int], sol: Solution, sky: Mask, cfg: DayCloudConfig
) -> _Geometry | None:
    h, w = key[0], key[1]
    sw = min(cfg.width, w)
    sh = max(1, round(h * sw / w))
    small = cv2.resize(sky.astype(np.uint8) * 255, (sw, sh), interpolation=cv2.INTER_AREA)
    ys, xs = np.nonzero(np.asarray(small) > 127)
    if xs.size == 0:
        return None
    # Pixel centres in the coordinates of the frame the orientation was solved on.
    scale = (sol.width or w) / sw
    px = (xs.astype(np.float64) + 0.5) * scale
    py = (ys.astype(np.float64) + 0.5) * scale
    alt, az = sol.unproject(px, py)
    up = alt >= cfg.min_alt
    a, z = np.radians(alt[up]), np.radians(az[up])
    vec = np.empty((a.size, 3), dtype=np.float32)
    vec[:, 0] = np.cos(a) * np.sin(z)
    vec[:, 1] = np.cos(a) * np.cos(z)
    vec[:, 2] = np.sin(a)
    index = (ys[up] * sw + xs[up]).astype(np.intp)
    return _Geometry(key, (sw, sh), index, vec)


def sun_vector(at: datetime, lat: float, lon: float) -> npt.NDArray[np.float32]:
    """Unit vector (east, north, up) to the sun."""
    ra, dec = sun_radec(at)
    alt, az = alt_az(ra, dec, sidereal_deg(at, lon), lat)
    a, z = math.radians(alt), math.radians(az)
    return np.array(
        [math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a)], dtype=np.float32
    )


def saturation(pix: npt.NDArray[np.uint8]) -> npt.NDArray[np.float32]:
    """HSV saturation (max - min)/max of (n, 3) RGB pixels; 0 for black."""
    mx = pix.max(axis=1)
    mn = pix.min(axis=1)
    return (mx - mn).astype(np.float32) / np.maximum(mx, 1).astype(np.float32)


def _pair_saturation() -> tuple[npt.NDArray[np.float32], npt.NDArray[np.intp]]:
    """The saturation of every (max, min) pair, index max·256 + min, and that index sorted
    by saturation: a frame's saturations are then one histogram of 65536 counts."""
    mx = np.arange(65536, dtype=np.int64) >> 8
    mn = np.arange(65536, dtype=np.int64) & 255
    sat = (np.maximum(mx - mn, 0) / np.maximum(mx, 1)).astype(np.float32)
    return sat, np.argsort(sat, kind="stable").astype(np.intp)


_PAIR_SAT, _PAIR_ORDER = _pair_saturation()


class SaturationHistory:
    """Frame medians of the saturation per solar day, as a histogram (SPEC §6.3 T).

    Bounded: at most `history_days` days of `bins` counts, stored sparsely. A day is the
    local solar date (UTC + longitude/15 h), so one day's daylight never spans two days.
    """

    def __init__(self, path: Path | None, cfg: DayCloudConfig) -> None:
        self._path = path
        self._cfg = cfg
        self._days: dict[str, dict[int, int]] = {}
        self._profile: str | None = None
        self._dirty = False
        self._saved_at: datetime | None = None
        self._threshold: float | None = None  # cached until the next add
        self._load()

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if raw.get("bins") != self._cfg.bins:
                return
            days = {
                str(d): {int(b): int(n) for b, n in counts.items()}
                for d, counts in raw["days"].items()
            }
            profile = raw.get("profile")
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            log.warning("day saturation history unreadable, starting again: %s", exc)
            return
        self._days = days
        self._profile = str(profile) if profile is not None else None

    def save(self) -> None:
        if self._path is None or not self._dirty:
            return
        body = {
            "bins": self._cfg.bins,
            "profile": self._profile,
            "days": {
                d: {str(b): n for b, n in sorted(c.items())} for d, c in sorted(self._days.items())
            },
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self._path)
            self._dirty = False
        except OSError as exc:
            log.warning("cannot save the day saturation history: %s", exc)

    @staticmethod
    def solar_day(at: datetime, lon: float) -> date:
        return (at + timedelta(hours=lon / 15.0)).date()

    def _prune(self, today: date) -> None:
        # From the newest day kept: a frame with a wrong clock (a Pi without a real-time
        # clock before NTP) neither wipes the history nor stays in it.
        newest = max([today.isoformat(), *self._days])
        oldest = date.fromisoformat(newest) - timedelta(days=self._cfg.history_days - 1)
        for d in [d for d in self._days if d < oldest.isoformat()]:
            del self._days[d]

    def add(self, at: datetime, lon: float, median: float, profile: str) -> None:
        cfg = self._cfg
        if self._profile != profile:  # another camera has other levels
            self._days, self._profile = {}, profile
        today = self.solar_day(at, lon)
        counts = self._days.setdefault(today.isoformat(), {})
        b = min(cfg.bins - 1, max(0, int(median * cfg.bins)))
        counts[b] = counts.get(b, 0) + 1
        self._prune(today)
        self._dirty = True
        self._threshold = None
        if self._saved_at is None or not (timedelta(0) <= at - self._saved_at < cfg.save_every):
            self.save()
            self._saved_at = at

    def days(self) -> int:
        return sum(1 for c in self._days.values() if c)

    def threshold(self, profile: str) -> float:
        """T: (p5 + p95 of the frame medians) / 2 after `min_days` days, else the default."""
        cfg = self._cfg
        if self._profile != profile or self.days() < cfg.min_days:
            return cfg.default_threshold
        if self._threshold is None:
            hist = np.zeros(cfg.bins, dtype=np.int64)
            for c in self._days.values():
                for b, n in c.items():
                    hist[b] += n
            cum = np.cumsum(hist)
            total = int(cum[-1])

            def quantile(q: float) -> float:
                b = int(np.searchsorted(cum, q * total, side="left"))
                return (min(b, cfg.bins - 1) + 0.5) / cfg.bins

            self._threshold = (quantile(0.05) + quantile(0.95)) / 2.0
        return self._threshold


class DayCloud:
    """SPEC §6.3 `cloud_cover` by day: one `measure` per day frame, from the detection thread."""

    def __init__(self, path: Path | None = None, cfg: DayCloudConfig | None = None) -> None:
        self._cfg = cfg or DayCloudConfig()
        self.history = SaturationHistory(path, self._cfg)
        self._geo: _Geometry | None = None
        self._geo_mask: Mask | None = None  # kept so that its id stays unique
        self._recent: deque[tuple[datetime, float]] = deque()
        # The used pixels for the last sun position; the sun moves 0.25° a minute, the
        # set is computed again once it has moved more than `_SUN_STEP_DEG`.
        self._sun: npt.NDArray[np.float32] | None = None
        self._used: npt.NDArray[np.intp] = np.zeros(0, dtype=np.intp)

    def frame_value(
        self,
        frame: FrameInfo,
        rgb: npt.NDArray[np.uint8],
        sky: Mask,
        sol: Solution,
        lat: float,
        lon: float,
    ) -> float | None:
        """This frame's share of unsaturated sky, None if it cannot be judged."""
        cfg = self._cfg
        if rgb.ndim != 3 or rgb.shape[2] < 3:
            return None  # a mono camera has no colour
        h, w = rgb.shape[0], rgb.shape[1]
        key = (h, w, sol, id(sky))
        if self._geo is None or self._geo.key != key or self._geo_mask is not sky:
            self._geo = _geometry(key, sol, sky, cfg)
            self._geo_mask = sky
            self._sun = None
        geo = self._geo
        if geo is None:
            return None
        sun = sun_vector(frame.captured_at, lat, lon)
        if self._sun is None or float(self._sun @ sun) < _SUN_STEP_COS:
            far = geo.vec @ sun < np.float32(math.cos(math.radians(cfg.sun_deg)))
            self._sun, self._used = sun, geo.index[far]
        n = int(self._used.size)
        if n == 0:
            return None
        small = (
            rgb[:, :, :3]
            if (w, h) == geo.size
            else cv2.resize(rgb[:, :, :3], geo.size, interpolation=cv2.INTER_AREA)
        )
        flat = np.asarray(small, dtype=np.uint8).reshape(-1, 3)
        r, g, b = flat[self._used, 0], flat[self._used, 1], flat[self._used, 2]
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        if int(np.count_nonzero(mx >= cfg.clip_level)) > cfg.clip_max * n:
            return None  # glare
        pairs = np.bincount((mx.astype(np.intp) << 8) | mn, minlength=65536)
        # The frame median of the saturation, from the pairs sorted by saturation.
        cum = np.cumsum(pairs[_PAIR_ORDER])
        median = float(_PAIR_SAT[_PAIR_ORDER[int(np.searchsorted(cum, (n + 1) // 2))]])
        self.history.add(frame.captured_at, lon, median, frame.profile)
        threshold = self.history.threshold(frame.profile)
        return float(pairs[np.less(_PAIR_SAT, threshold)].sum()) / n

    def measure(
        self,
        frame: FrameInfo,
        rgb: npt.NDArray[np.uint8],
        sky: Mask,
        sol: Solution,
        lat: float,
        lon: float,
    ) -> float | None:
        """The median of the day values of the last 15 minutes, None for an unusable frame."""
        value = self.frame_value(frame, rgb, sky, sol, lat, lon)
        at = frame.captured_at
        recent = self._recent
        while recent and not (timedelta(0) <= at - recent[0][0] < self._cfg.window):
            recent.popleft()
        if value is None:
            return None
        recent.append((at, value))
        return round(statistics.median(v for _, v in recent), 3)

    def save(self) -> None:
        self.history.save()
