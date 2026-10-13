"""Read side of the camera's own storage for the local UI (roadmap L3–L6).

Everything here works without the hub: nights, frames, night products, detections and the
sky log come from the data directory (SPEC §4.5, §5).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from allskyhub_agent.detect.events import EventStore
from allskyhub_agent.products.build import night_products, product_path
from allskyhub_agent.products.day import MINI
from allskyhub_agent.products.keolapse import keolapse
from allskyhub_agent.store.images import ImageStore

LOOP_WINDOW = timedelta(hours=1)
MAX_FRAMES = 2000  # one night at 15 s pauses; the UI pages through more


class LocalArchive:
    def __init__(self, store: ImageStore, events: EventStore | None = None) -> None:
        self._store = store
        self._events = events
        self._root = store.images_dir.parent  # the data directory

    def nights(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for night in self._store.nights():
            frames = self._store.read_index(night)
            good = [f for f in frames if f.bad is None] or frames
            products = night_products(self._store, night)
            events = self._events.night_events(night) if self._events is not None else []
            if not frames and products is None and not events:
                continue
            out.append(
                {
                    "night_id": night,
                    "frames": len(frames),
                    "first": frames[0].captured_at.isoformat() if frames else None,
                    "last": frames[-1].captured_at.isoformat() if frames else None,
                    "bad": len(frames) - sum(f.bad is None for f in frames),
                    "cover": good[len(good) // 2].name if good else None,
                    "products": [p.model_dump(mode="json") for p in products.products]
                    if products is not None
                    else [],
                    "events": len(events),
                }
            )
        return out

    def frames(
        self, night: str, offset: int = 0, limit: int = MAX_FRAMES, bad: bool = False
    ) -> dict[str, Any]:
        """A page of the night's frames; bad frames (SPEC §4.9) only with `bad`."""
        frames = self._store.read_index(night) if night.isdigit() and len(night) == 8 else []
        if not bad:
            frames = [f for f in frames if f.bad is None]
        page = frames[max(0, offset) : max(0, offset) + max(1, min(limit, MAX_FRAMES))]
        return {"total": len(frames), "frames": [f.model_dump(mode="json") for f in page]}

    def loop(self, now: datetime) -> list[dict[str, Any]]:
        """Frames of the last hour, oldest first (the live loop, roadmap L3)."""
        since = now - LOOP_WINDOW
        out: list[dict[str, Any]] = []
        for night in self._store.nights()[:2]:  # the hour can reach back into the last night
            out += [
                f.model_dump(mode="json")
                for f in self._store.read_index(night)
                if f.captured_at >= since and f.bad is None
            ]
        out.sort(key=lambda f: f["captured_at"])
        return out

    def events(self, night: str) -> list[dict[str, Any]]:
        if self._events is None or not (night.isdigit() and len(night) == 8):
            return []
        events = sorted(self._events.night_events(night), key=lambda e: e.start)
        return [e.model_dump(mode="json") for e in events]

    def sky(self, night: str) -> list[dict[str, Any]]:
        return [m.model_dump(mode="json") for m in self._store.read_sky(night)]

    def image(self, night: str, name: str, thumbnail: bool) -> Path | None:
        return self._store.path_for(night, name, thumbnail)

    def product(self, night: str, name: str, thumbnail: bool) -> Path | None:
        return product_path(self._store, night, name, thumbnail)

    def keolapse(self, first: str, last: str) -> bytes | None:
        """Keograms of a range of nights side by side (SPEC §5.4)."""
        return keolapse(self._store, first, last, self._root / "cache")

    def mini(self) -> Path | None:
        """The mini timelapse of the last hour (SPEC §5.2), if there is one."""
        path = self._root / "live" / MINI
        return path if path.is_file() else None

    def event_image(self, night: str, eid: str, thumbnail: bool) -> Path | None:
        if self._events is None:
            return None
        return self._events.image_path(night, eid, thumbnail)
