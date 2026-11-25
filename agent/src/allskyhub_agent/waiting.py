"""What the camera waits for (SPEC §6.3 `waiting`): no place from setup yet, or a camera
that fails to open or to capture 3 times in a row."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from allskyhub_protocol import Waiting

# SPEC §6.3: a camera failure is reported from the third in a row.
CAMERA_FAILURES = 3
MESSAGE_MAX = 200


class WaitState:
    """Shared between the capture side, which reports, and `status`, which reads."""

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._location_since: datetime | None = None
        self._failures = 0
        self._failing_since: datetime | None = None
        self._error = ""

    def waiting_for_location(self, waiting: bool) -> None:
        with self._lock:
            if not waiting:
                self._location_since = None
            elif self._location_since is None:
                self._location_since = self._now()

    def camera_failed(self, error: object) -> None:
        """One failed attempt to open or capture."""
        with self._lock:
            if self._failures == 0:
                self._failing_since = self._now()
            self._failures += 1
            self._error = str(error)

    def camera_ok(self) -> None:
        """A good frame: the row of failures ends."""
        with self._lock:
            self._failures = 0
            self._failing_since = None
            self._error = ""

    @property
    def current(self) -> Waiting | None:
        with self._lock:
            if self._location_since is not None:
                return Waiting(reason="location", since=self._location_since)
            if self._failures >= CAMERA_FAILURES and self._failing_since is not None:
                message = self._error.strip()[:MESSAGE_MAX] or None
                return Waiting(reason="camera", since=self._failing_since, message=message)
            return None
