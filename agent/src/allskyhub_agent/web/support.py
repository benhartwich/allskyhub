"""Logs, support bundle, backup and restore for the local UI (roadmap L15).

The agent keeps its own recent log lines in memory: the local UI shows them without
access to the system journal, which the agent's user cannot read. The support bundle is
a ZIP to attach to a question: log, versions, system and network facts, settings, the
updater's state and the calibration, never keys, sessions or the offline code. The
backup holds what the owner set on this camera (settings and drawn sky-mask areas) to
put onto a new card.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import zipfile
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

LOG_LINES = 2000
BACKUP_KIND = "allskyhub-backup"


class RecentLog(logging.Handler):
    """The last LOG_LINES records, formatted like the journal lines."""

    def __init__(self, size: int = LOG_LINES) -> None:
        super().__init__(logging.INFO)
        self._lines: deque[str] = deque(maxlen=size)
        self._lock_lines = threading.Lock()
        self._seq = 0  # number of lines ever logged, for following the log
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock_lines:
            self._lines.append(line)
            self._seq += 1

    def lines(self, n: int = LOG_LINES) -> list[str]:
        with self._lock_lines:
            return list(self._lines)[-n:]

    def since(self, seq: int) -> tuple[int, list[str]]:
        """The lines after `seq` (at most what is kept) and the new `seq`, to follow live."""
        with self._lock_lines:
            new = max(0, min(self._seq - seq, len(self._lines)))
            return self._seq, list(self._lines)[len(self._lines) - new :] if new else []


@dataclass(frozen=True)
class Support:
    log: RecentLog
    # Each part of the bundle as a JSON-able value; a part that fails is noted, not fatal.
    facts: Mapping[str, Callable[[], object]]
    # Files copied into the bundle as they are (calibration, updater state).
    files: Mapping[str, Path]
    settings: Callable[[], dict[str, Any]]
    mask_edits: Callable[[], dict[str, Any]]
    # Applies a backup: the settings (checked like any change) and the drawn areas;
    # returns the invalid keys.
    restore: Callable[[dict[str, Any], dict[str, Any] | None], list[str]]

    def bundle(self, now: datetime | None = None) -> bytes:
        now = now or datetime.now(UTC)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("agent.log", "\n".join(self.log.lines()) + "\n")
            parts: dict[str, object] = {"created_at": now.isoformat()}
            for name, read in self.facts.items():
                try:
                    parts[name] = read()
                except Exception as exc:
                    parts[name] = f"error: {exc}"
            parts["settings"] = self.settings()
            z.writestr("facts.json", json.dumps(parts, indent=2, default=str))
            for name, path in self.files.items():
                try:
                    z.writestr(name, path.read_bytes())
                except OSError:
                    continue
        return buf.getvalue()

    def backup(self, now: datetime | None = None) -> dict[str, Any]:
        return {
            "kind": BACKUP_KIND,
            "created_at": (now or datetime.now(UTC)).isoformat(),
            "settings": self.settings(),
            "skymask_edits": self.mask_edits(),
        }

    def apply(self, raw: object) -> list[str]:
        """Restore a backup; the invalid keys, ["backup"] if it is not one."""
        if not isinstance(raw, dict):
            return ["backup"]
        d = cast("dict[str, Any]", raw)
        settings: object = d.get("settings")
        edits: object = d.get("skymask_edits")
        if d.get("kind") != BACKUP_KIND or not isinstance(settings, dict):
            return ["backup"]
        return self.restore(
            cast("dict[str, Any]", settings),
            cast("dict[str, Any]", edits) if isinstance(edits, dict) else None,
        )
