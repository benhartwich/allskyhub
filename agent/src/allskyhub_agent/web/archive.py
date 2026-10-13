"""Read side of the camera's own storage for the local UI (roadmap L3–L6).

Everything here works without the hub: nights, frames, night products, detections and the
sky log come from the data directory (SPEC §4.5, §5).
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from allskyhub_agent.detect.events import EventStore
from allskyhub_agent.detect.labels import Labels
from allskyhub_agent.products.build import night_products, product_path
from allskyhub_agent.products.day import MINI
from allskyhub_agent.products.keolapse import keolapse
from allskyhub_agent.store.images import ImageStore, night_id
from allskyhub_protocol import EventLabel, FrameInfo, NightDeleted

LOOP_WINDOW = timedelta(hours=1)
DELETED_NAME = "deleted-nights.json"
DELETED_KEEP = timedelta(days=60)
MAX_FRAMES = 2000  # one night at 15 s pauses; the UI pages through more


class LocalArchive:
    def __init__(
        self,
        store: ImageStore,
        events: EventStore | None = None,
        labels: Labels | None = None,
        on_label: Callable[[EventLabel], None] | None = None,
        on_deleted: Callable[[NightDeleted], None] | None = None,
    ) -> None:
        self._on_deleted = on_deleted
        self._store = store
        self._events = events
        self._labels = labels
        self._on_label = on_label
        self._root = store.images_dir.parent  # the data directory

    def nights(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for night in self._store.nights():
            frames = self._store.read_index(night)
            good = [f for f in frames if f.bad is None] or frames
            products = night_products(self._store, night)
            events = self._events.night_events(night) if self._events is not None else []
            # SPEC §6.4 Labels: false positives count nowhere.
            fp: set[str] = self._labels.false_positives(night) if self._labels else set()
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
                    "events": sum(e.id not in fp for e in events),
                }
            )
        return out

    def frames(
        self,
        night: str,
        offset: int = 0,
        limit: int = MAX_FRAMES,
        bad: bool = False,
        at: str | None = None,
    ) -> dict[str, Any]:
        """A page of the night's frames; bad frames (SPEC §4.9) only with `bad`.

        `at` ("HH:MM", camera time) starts the page at the first frame from then on, for
        jumping to a time: afternoon and evening belong to the night's date, the hours
        before noon to the next morning (SPEC §4.5).
        """
        frames = self._store.read_index(night) if night.isdigit() and len(night) == 8 else []
        if not bad:
            frames = [f for f in frames if f.bad is None]
        if at is not None:
            when = self._night_time(night, at)
            if when is not None:
                offset = next(
                    (i for i, f in enumerate(frames) if f.captured_at >= when), len(frames)
                )
                offset = max(0, min(offset, len(frames) - 1))
        offset = max(0, offset)
        page = frames[offset : offset + max(1, min(limit, MAX_FRAMES))]
        return {
            "total": len(frames),
            "offset": offset,
            "frames": [f.model_dump(mode="json") for f in page],
        }

    def _night_time(self, night: str, at: str) -> datetime | None:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", at)
        if m is None or int(m[1]) > 23 or int(m[2]) > 59:
            return None
        try:
            day = datetime.strptime(night, "%Y%m%d").date()  # noqa: DTZ007
        except ValueError:
            return None
        if int(m[1]) < 12:
            day += timedelta(days=1)
        local = datetime(day.year, day.month, day.day, int(m[1]), int(m[2]))  # noqa: DTZ001
        return local.replace(tzinfo=self._store.local_tz)

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
        frames = self._store.read_index(night)
        labels = self._labels.night(night) if self._labels is not None else {}
        out: list[dict[str, Any]] = []
        for e in events:
            d = e.model_dump(mode="json")
            d["frame"] = _frame_of(frames, e.start)
            lab = labels.get(e.id)
            d["label"] = lab.label if lab is not None else None
            d["reason"] = lab.reason if lab is not None else None
            d["label_auto"] = lab.auto if lab is not None else False
            d["label_detail"] = lab.detail if lab is not None else None
            out.append(d)
        return out

    def set_label(self, night: str, event_id: str, body: object) -> EventLabel | None:
        """The owner's label from the local UI (SPEC §6.4 Labels); None if the event is
        unknown, ValueError if the body is not a label."""
        if self._labels is None or self._events is None:
            return None
        if not any(e.id == event_id for e in self._events.night_events(night)):
            return None
        if not isinstance(body, dict):
            raise ValueError("label")
        raw = cast("dict[str, Any]", body)
        label = EventLabel.model_validate(
            {
                "night_id": night,
                "event_id": event_id,
                "label": raw.get("label"),
                "reason": raw.get("reason"),
                "at": datetime.now(UTC),
            }
        )
        self._labels.set(label)
        if self._on_label is not None:
            self._on_label(label)
        return label

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

    # --- deleting a night (SPEC §7, archive maintenance) ----------------------------------
    def delete_night(self, night: str, now: datetime) -> str | None:
        """Remove a whole night; None when done, else `current` or `not_found`."""
        if not (night.isdigit() and len(night) == 8):
            return "not_found"
        if night == night_id(now, self._store.local_tz):
            return "current"
        folder = self._store.night_dir(night)
        if not folder.is_dir():
            return "not_found"
        shutil.rmtree(folder, ignore_errors=True)
        deleted = self._deleted()
        deleted[night] = now.isoformat()
        tmp = self._root / (DELETED_NAME + ".tmp")
        tmp.write_text(json.dumps(deleted), encoding="utf-8")
        tmp.replace(self._root / DELETED_NAME)
        if self._on_deleted is not None:
            self._on_deleted(NightDeleted(night_id=night))
        return None

    def _deleted(self) -> dict[str, str]:
        try:
            raw: object = json.loads((self._root / DELETED_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in cast("dict[str, Any]", raw).items()}

    def deleted_nights(self, now: datetime) -> list[NightDeleted]:
        """Nights deleted in the last 60 days, sent again after every hello."""
        out: list[NightDeleted] = []
        for night, at in self._deleted().items():
            try:
                when = datetime.fromisoformat(at)
            except ValueError:
                continue
            if now - when <= DELETED_KEEP:
                out.append(NightDeleted(night_id=night))
        return out


def _frame_of(frames: list[FrameInfo], at: datetime) -> str | None:
    """The stored frame an event was seen on: the one starting at the event's start, else
    the nearest within 2 minutes (imported events carry the other program's times)."""
    best: tuple[float, str] | None = None
    for f in frames:
        gap = abs((f.captured_at - at).total_seconds())
        if gap <= 120 and (best is None or gap < best[0]):
            best = (gap, f.name)
    return best[1] if best is not None else None
