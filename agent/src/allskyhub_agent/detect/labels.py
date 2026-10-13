"""The owner's labels of detections (SPEC §6.4 Labels).

`confirmed` or `false_positive` (with a reason), set in the local UI or on the hub; the
newer `at` wins. An automatic label (`auto`, SPEC §6.4, §7.8, §7.9) never replaces the
owner's, which always replaces it. Kept per night in
`<data>/images/<night>/events/labels.json` next to the events; a false positive is never
deleted, only hidden and left out of counts.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, cast

from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import EventLabel

LABELS_NAME = "labels.json"


class Labels:
    def __init__(self, store: ImageStore) -> None:
        self._store = store
        self._lock = threading.Lock()

    def _path(self, night: str) -> Path:
        return self._store.night_dir(night) / "events" / LABELS_NAME

    def night(self, night: str) -> dict[str, EventLabel]:
        """The labels of a night by event id (removed labels included, `label` null)."""
        try:
            raw: object = json.loads(self._path(night).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, EventLabel] = {}
        for eid, v in cast("dict[str, Any]", raw).items():
            try:
                out[eid] = EventLabel.model_validate(v)
            except ValueError:
                continue
        return out

    def set(self, label: EventLabel) -> bool:
        """Store it unless the stored one wins (SPEC §6.4 Labels); True if stored."""
        with self._lock:
            labels = self.night(label.night_id)
            if not label.replaces(labels.get(label.event_id)):
                return False
            labels[label.event_id] = label
            path = self._path(label.night_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({k: v.model_dump(mode="json") for k, v in labels.items()}),
                encoding="utf-8",
            )
            tmp.replace(path)
            return True

    def all(self) -> list[EventLabel]:
        """Every stored label, for sending them again after a reconnect."""
        out: list[EventLabel] = []
        for night in self._store.nights():
            out.extend(self.night(night).values())
        return out

    def false_positives(self, night: str) -> set[str]:
        return {k for k, v in self.night(night).items() if v.label == "false_positive"}
