"""Background worker for night products (SPEC §5.2): capture never waits for it."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from allskyhub_agent.products.build import NightProducts, ProductConfig, build_night
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import FrameInfo, Mode

log = logging.getLogger(__name__)


class DawnDetector:
    """Says which night just ended when the mode switches from night to day."""

    def __init__(self) -> None:
        self._prev: FrameInfo | None = None

    def feed(self, info: FrameInfo) -> str | None:
        prev, self._prev = self._prev, info
        if prev is not None and prev.mode is Mode.NIGHT and info.mode is Mode.DAY:
            return prev.night_id
        return None


class ProductWorker:
    def __init__(
        self,
        store: ImageStore,
        cfg: ProductConfig | None = None,
        on_done: Callable[[NightProducts], None] | None = None,
        keep_days: int | None = 14,
        min_free_pct: float | None = 10.0,
        free_check_s: float = 600.0,
    ) -> None:
        self._store = store
        self._keep_days = keep_days
        self._min_free_pct = min_free_pct
        self._free_check_s = free_check_s
        self._cfg = cfg or ProductConfig()
        self._on_done = on_done
        self._q: queue.Queue[str | None] = queue.Queue()
        self._dawn = DawnDetector()
        self._thread = threading.Thread(target=self._run, name="products", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def on_frame(self, info: FrameInfo) -> None:
        """Called for every frame from the capture thread; never blocks."""
        night = self._dawn.feed(info)
        if night is not None:
            self.submit(night)

    def submit(self, night: str) -> None:
        self._q.put(night)

    def _run(self) -> None:
        while True:
            try:
                night = self._q.get(timeout=self._free_check_s)
            except queue.Empty:
                self._check_free()  # every few minutes, so a full card never stops capture
                continue
            if night is None:
                return
            try:
                result = build_night(self._store, night, self._cfg)
                log.info("night %s: built %s from %d frames", night, result.built, result.frames)
                if self._on_done is not None:
                    self._on_done(result)
            except Exception:  # a broken night must not stop later ones
                log.exception("building products of night %s failed", night)
            if self._keep_days is not None:
                # Retention by days (SPEC §4.5), once a day after the products are done.
                try:
                    removed = self._store.cleanup(datetime.now(UTC), self._keep_days)
                    if removed:
                        log.info("removed old nights %s", removed)
                except OSError:
                    log.exception("cleanup failed")
            self._check_free()

    def _check_free(self) -> None:
        if self._min_free_pct is None:
            return
        try:
            removed = self._store.ensure_free(datetime.now(UTC), self._min_free_pct)
        except OSError:
            log.exception("free-space check failed")
            return
        if removed:
            log.warning("disk almost full: removed nights %s", removed)

    def stop(self, timeout: float = 5.0) -> None:
        self._q.put(None)
        self._thread.join(timeout)
