"""Old nights still to announce to the hub, kept across restarts (SPEC §6.7, roadmap L18)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

BACKLOG_NAME = "hub_backlog.json"


class Backlog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _read(self) -> list[str]:
        try:
            data: object = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return []
        items = cast(list[Any], data) if isinstance(data, list) else []
        return [n for n in items if isinstance(n, str) and len(n) == 8 and n.isdigit()]

    def _write(self, nights: list[str]) -> None:
        if not nights:
            self._path.unlink(missing_ok=True)
            return
        # A fresh, random name (O_EXCL): the import runs as root in the agent's folder and
        # must never write through a link planted there.
        fd, name = tempfile.mkstemp(prefix=".backlog-", dir=self._path.parent)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(nights))
        Path(name).replace(self._path)

    def add(self, nights: list[str], owner: tuple[int, int] | None = None) -> None:
        with self._lock:
            merged = sorted(set(self._read()) | set(nights))
            self._write(merged)
            if owner is not None and self._path.exists() and os.geteuid() == 0:
                os.chown(self._path, *owner, follow_symlinks=False)

    def next(self) -> str | None:
        with self._lock:
            nights = self._read()
            return nights[0] if nights else None

    def done(self, night: str) -> None:
        with self._lock:
            self._write([n for n in self._read() if n != night])
