"""Since when the hub has the camera's labels (SPEC §6.4 Labels: after a reconnect only
those set while offline are sent again).

2026-11-20: every reconnect sent all 4056 labels of 35 nights; the hub's rate limit held its
reader for a minute, pings went unanswered and the camera reconnected again.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from pathlib import Path

# A clock that was set back a little must not lose a label.
MARGIN = timedelta(minutes=10)
SYNC_MARK_NAME = "hub-labels-synced"


class SyncMark:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self) -> datetime | None:
        """Labels set at or after this go again; None: all of them."""
        try:
            at = datetime.fromisoformat(self._path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        if at.tzinfo is None:
            return None
        return at - MARGIN

    def start(self, now: datetime, back: timedelta = timedelta(days=1)) -> None:
        """Up to 0.1.9 every reconnect sent all labels, so the hub has those before the last
        day: without a mark yet, start there instead of sending thousands again."""
        if not self._path.exists():
            self.set(now - back)

    def set(self, at: datetime) -> None:
        """A connection that started at `at` stayed up: everything set before it is there."""
        tmp = self._path.with_suffix(".tmp")
        with contextlib.suppress(OSError):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(at.isoformat(), encoding="utf-8")
            tmp.replace(self._path)
