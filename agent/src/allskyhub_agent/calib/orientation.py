"""When to plate-solve and what status reports (SPEC §4.8).

The solve takes about 20 s on a Pi 4, so it runs in its own thread on a stored frame:
a clear frame at astronomical night (sky meter: little cloud, many stars) with a second
clear frame 20-40 min before it, at most a few tries per night, and again after a week
to notice a camera that was bumped or turned. The result is kept on disk.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from allskyhub_agent.calib.solve import Solution, solve
from allskyhub_protocol import FrameInfo, Orientation, SkyMetrics

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrientationConfig:
    max_sun_deg: float = -18.0
    max_cloud: float = 0.1
    min_stars: int = 150
    pair_min_s: float = 1200.0  # the second frame: 20 to 40 min earlier
    pair_max_s: float = 2400.0
    retry_s: float = 1800.0  # between tries in one night
    max_tries: int = 3  # per night
    resolve_after: timedelta = timedelta(days=7)


class Orienter:
    def __init__(
        self,
        path: Path,
        location: Callable[[], tuple[float, float] | None],
        time_trusted: Callable[[], bool] = lambda: True,
        cfg: OrientationConfig | None = None,
    ) -> None:
        self._path = path
        self._location = location
        self._time_trusted = time_trusted
        self._cfg = cfg or OrientationConfig()
        self._lock = threading.Lock()
        self._solution: Solution | None = None
        self._solved_at: datetime | None = None
        self._clear: list[tuple[datetime, Path]] = []
        self._night: str | None = None
        self._tries = 0
        self._last_try: datetime | None = None
        self._thread: threading.Thread | None = None
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            sol = Solution(**{k: v for k, v in raw.items() if k != "solved_at"})
            at = datetime.fromisoformat(raw["solved_at"])
        except (OSError, ValueError, TypeError, KeyError):
            return
        self._solution, self._solved_at = sol, at

    @property
    def solution(self) -> Solution | None:
        with self._lock:
            return self._solution

    @property
    def latest(self) -> Orientation | None:
        """For status (SPEC §6.3)."""
        with self._lock:
            sol, at = self._solution, self._solved_at
        if sol is None or at is None:
            return None
        return Orientation(
            north_deg=round(sol.rot % 360.0, 1),
            mirrored=sol.flip < 0,
            solved_at=at,
            stars=sol.stars,
            rms_deg=sol.rms_deg,
        )

    def busy(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def consider(self, frame: FrameInfo, image_path: Path, sky: SkyMetrics | None) -> bool:
        """Called for every stored frame; starts a solve when it is time. True if started."""
        cfg = self._cfg
        if frame.night_id != self._night:
            self._night, self._clear, self._tries, self._last_try = frame.night_id, [], 0, None
        clear = (
            frame.sun_elevation <= cfg.max_sun_deg
            and sky is not None
            and sky.cloud_cover is not None
            and sky.cloud_cover <= cfg.max_cloud
            and (sky.stars or 0) >= cfg.min_stars
        )
        if not clear:
            return False
        at = frame.captured_at
        self._clear.append((at, image_path))
        self._clear = [c for c in self._clear if (at - c[0]).total_seconds() <= cfg.pair_max_s]
        with self._lock:
            solved_at = self._solved_at
        if solved_at is not None and at - solved_at < cfg.resolve_after:
            return False
        if self._tries >= cfg.max_tries or self.busy():
            return False
        if self._last_try is not None and (at - self._last_try).total_seconds() < cfg.retry_s:
            return False
        earlier = [c for c in self._clear if (at - c[0]).total_seconds() >= cfg.pair_min_s]
        loc = self._location()
        if not earlier or loc is None or not self._time_trusted():
            return False
        self._tries += 1
        self._last_try = at
        # Stars at mid-exposure: they move up to 0.4° during a 90 s exposure.
        mid = at + timedelta(microseconds=frame.exposure_us // 2)
        self._thread = threading.Thread(
            target=self._solve, args=(image_path, earlier[0][1], mid, loc), name="solve",
            daemon=True,
        )  # fmt: skip
        self._thread.start()
        return True

    def _solve(self, path: Path, static_path: Path, at: datetime, loc: tuple[float, float]) -> None:
        try:
            gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            static = cv2.imread(str(static_path), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                return
            sol = solve(
                np.asarray(gray, dtype=np.uint8),
                at,
                loc[0],
                loc[1],
                np.asarray(static, dtype=np.uint8) if static is not None else None,
            )
        except Exception:  # a broken frame must not stop the agent
            log.exception("plate solve failed for %s", path.name)
            return
        if sol is None:
            log.info("plate solve: no clear solution for %s", path.name)
            return
        self.store(sol, at)
        log.info("plate solve: north at %.1f°, %d stars, %.2f°", sol.rot, sol.stars, sol.rms_deg)

    def store(self, sol: Solution, at: datetime) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.stem + ".tmp.json")
        tmp.write_text(json.dumps({**asdict(sol), "solved_at": at.isoformat()}), encoding="utf-8")
        tmp.replace(self._path)
        with self._lock:
            self._solution, self._solved_at = sol, at

    def join(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)
