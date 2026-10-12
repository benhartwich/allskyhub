"""When the sky is safe for a telescope (SPEC §7.5 `issafe`).

Fed with every measured frame and every lightning; asked with the current time. Safe only
at night with a fresh measurement, little cloud and no lightning lately. Cloud has
hysteresis: unsafe after two frames in a row at or above the limit, safe again only after
the cover stayed at least 10 points below it for the hold time.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from allskyhub_protocol import SkyMetrics

STALE = timedelta(minutes=10)
LIGHTNING_HOLD = timedelta(minutes=30)
MARGIN = 0.10  # cloud cover must fall this far below the limit to count as clearing


@dataclass(frozen=True)
class SafetyConfig:
    cloud_max: float = 0.30  # setting safe_cloud_max, as a fraction
    hold: timedelta = timedelta(minutes=10)  # setting safe_hold_min


class SafetyMonitor:
    def __init__(self, cfg: SafetyConfig | None = None) -> None:
        self._cfg = cfg or SafetyConfig()
        self._lock = threading.Lock()
        self._latest: SkyMetrics | None = None
        self._cloudy = True  # until the sky has been clear for the hold time
        self._over = 0  # frames in a row at or above the limit
        self._clear_since: datetime | None = None
        self._lightning: datetime | None = None

    def observe(self, m: SkyMetrics) -> None:
        """Every measured frame, in order."""
        cfg = self._cfg
        with self._lock:
            self._latest = m
            c = m.cloud_cover
            if c is None:  # day or twilight: nothing to judge the cloud by
                self._over, self._clear_since, self._cloudy = 0, None, True
                return
            if c >= cfg.cloud_max:
                self._over += 1
                self._clear_since = None
                if self._over >= 2:
                    self._cloudy = True
                return
            self._over = 0
            if c > cfg.cloud_max - MARGIN:
                self._clear_since = None  # below the limit, but not clearly: no change
                return
            if self._clear_since is None:
                self._clear_since = m.at
            if m.at - self._clear_since >= cfg.hold:
                self._cloudy = False

    def lightning(self, at: datetime) -> None:
        with self._lock:
            if self._lightning is None or at > self._lightning:
                self._lightning = at

    def latest(self) -> SkyMetrics | None:
        with self._lock:
            return self._latest

    def is_safe(self, now: datetime) -> bool:
        with self._lock:
            m = self._latest
            if m is None or m.cloud_cover is None or now - m.at > STALE:
                return False
            if self._lightning is not None and now - self._lightning < LIGHTNING_HOLD:
                return False
            return not self._cloudy
