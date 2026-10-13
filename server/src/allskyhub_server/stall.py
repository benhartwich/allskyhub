"""Finds what blocks the event loop: a page that takes seconds while the hub is idle.

The loop marks a heartbeat every ``BEAT_S``; a thread checks it, and when the loop has not
beaten for ``STALL_S``, it logs the loop thread's stack once per stall. Costs one wake-up of
each every 0.25 s.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger(__name__)

BEAT_S = 0.25
STALL_S = 1.0


class StallWatch:
    def __init__(self, stall_s: float = STALL_S) -> None:
        self._stall_s = stall_s
        self._beat = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop_thread = threading.get_ident()
        self.stalls = 0

    async def run_forever(self) -> None:
        self._loop_thread = threading.get_ident()
        self._thread = threading.Thread(target=self._watch, name="stall-watch", daemon=True)
        self._thread.start()
        try:
            while True:
                self._beat = time.monotonic()
                await asyncio.sleep(BEAT_S)
        finally:
            self._stop.set()

    def _watch(self) -> None:
        reported = 0.0
        while not self._stop.wait(BEAT_S):
            beat = self._beat
            late = time.monotonic() - beat
            if late < self._stall_s or beat == reported:
                continue
            reported = beat
            self.stalls += 1
            frame = sys._current_frames().get(self._loop_thread)  # pyright: ignore[reportPrivateUsage]
            stack = "".join(traceback.format_stack(frame, limit=25)) if frame else ""
            log.warning("event loop blocked", extra={"blocked_s": round(late, 2), "stack": stack})
