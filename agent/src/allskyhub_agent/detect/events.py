"""Detections on disk and in the background (SPEC §6.4): pictures, index, worker thread.

Per night: `events/<id>.jpg` (a crop around the detection), `events/thumbnails/<id>.jpg`
and `events.jsonl` with one `event` body per line.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image as PILImage
from pydantic import ValidationError

from allskyhub_agent.adapters.camera import Image
from allskyhub_agent.calib.orientation import Orienter
from allskyhub_agent.calib.solve import Solution
from allskyhub_agent.core.metering import Mask
from allskyhub_agent.detect.aurora import AuroraConfig, AuroraDetector, AuroraUpdate
from allskyhub_agent.detect.episodes import EpisodeUpdate
from allskyhub_agent.detect.lightning import LightningConfig, LightningDetector, LightningHit
from allskyhub_agent.detect.meteor import (
    MeteorConfig,
    MeteorDetector,
    MeteorHit,
    Streak,
    active_shower,
    match_shower,
    to_gray,
)
from allskyhub_agent.detect.nlc import NlcDetector, NlcUpdate
from allskyhub_agent.detect.sky import SkyMeter
from allskyhub_agent.process.skymask import SkyMask
from allskyhub_agent.store.images import THUMB_WIDTH, ImageStore
from allskyhub_protocol import Event, EventKind, FrameInfo, Mode, event_id

log = logging.getLogger(__name__)

INDEX = "events.jsonl"
CROP_MIN_PX = 480  # the picture shows the meteor with some sky around it
FULL_MAX_PX = 1920  # a lightning picture is the whole frame, at most this wide


class EventStore:
    def __init__(
        self,
        store: ImageStore,
        orientation: Callable[[], Solution | None] | None = None,
        location: Callable[[], tuple[float, float] | None] | None = None,
    ) -> None:
        self._store = store
        self._lock = threading.Lock()
        # SPEC §4.8: the orientation valid when an event is saved gives its sky position.
        self._orientation = orientation
        self._location = location

    def _shower(self, f: FrameInfo, s: Streak) -> dict[str, str | None]:
        """`shower` and `shower_match` (SPEC §6.4): by the radiant once the camera is
        oriented, else by the date alone."""
        sol = self._orientation() if self._orientation is not None else None
        loc = self._location() if self._location is not None else None
        size = self._frame_size(f) if sol is not None and loc is not None else None
        if sol is not None and loc is not None and size is not None:
            e1, e2 = sol.position(*s.p1, *size), sol.position(*s.p2, *size)
            if e1 is not None and e2 is not None:
                mid = f.captured_at + timedelta(microseconds=f.exposure_us // 2)
                return {"shower": match_shower(e1, e2, mid, *loc), "shower_match": "radiant"}
        name = active_shower(f.captured_at.date())
        return {"shower": name, "shower_match": "date" if name else None}

    def _position(
        self, frame: FrameInfo, x: float | None, y: float | None
    ) -> dict[str, float | None]:
        """`azimuth_deg` and `altitude_deg` of a pixel of the frame, null without an
        orientation (SPEC §6.4)."""
        sol = self._orientation() if self._orientation is not None else None
        pos = None
        if sol is not None and x is not None and y is not None:
            size = self._frame_size(frame)
            pos = sol.position(x, y, *size) if size is not None else None
        az, alt = pos if pos is not None else (None, None)
        return {"azimuth_deg": az, "altitude_deg": alt}

    def _frame_size(self, frame: FrameInfo) -> tuple[int, int] | None:
        try:
            with PILImage.open(self._store.night_dir(frame.night_id) / frame.name) as im:
                return im.width, im.height
        except OSError:
            return None

    def _dir(self, night: str) -> Path:
        return self._store.night_dir(night) / "events"

    def night_events(self, night: str) -> list[Event]:
        out: list[Event] = []
        try:
            lines = (self._store.night_dir(night) / INDEX).read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        by_id: dict[str, Event] = {}
        for line in lines:
            try:
                e = Event.model_validate_json(line)
            except ValidationError:
                continue
            by_id[e.id] = e  # an episode is appended again as it grows: the last one counts
        return list(by_id.values())

    def current_events(self) -> list[Event]:
        """The newest night's events (resent after every reconnect, SPEC §6.7)."""
        root = self._store.images_dir
        if not root.is_dir():
            return []
        nights = sorted(
            (p.name for p in root.iterdir() if p.is_dir() and p.name.isdigit()), reverse=True
        )
        for night in nights[:2]:  # today's night may not have any yet
            events = self.night_events(night)
            if events:
                return events
        return []

    def image_path(self, night: str, eid: str, thumbnail: bool) -> Path | None:
        if not (len(night) == 8 and night.isdigit()) or "/" in eid or eid.startswith("."):
            return None
        d = self._dir(night)
        p = (d / "thumbnails" / f"{eid}.jpg") if thumbnail else (d / f"{eid}.jpg")
        return p if p.is_file() else None

    def _new_id(self, kind: EventKind, f: FrameInfo) -> str:
        known = {e.id for e in self.night_events(f.night_id)}
        seq = 1
        eid = event_id(kind, f.captured_at)
        while eid in known:
            seq += 1
            eid = event_id(kind, f.captured_at, seq)
        return eid

    def _append(self, event: Event) -> None:
        path = self._store.night_dir(event.night_id) / INDEX
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")

    def save_meteor(self, hit: MeteorHit) -> Event:
        """Crop the frame around the streak, store picture, thumbnail and index line."""
        f, s = hit.frame, hit.streak
        with self._lock:
            eid = self._new_id(EventKind.METEOR, f)
            has_image = self._save_crop(hit, f.night_id, eid)
            event = Event(
                id=eid,
                night_id=f.night_id,
                kind=EventKind.METEOR,
                start=f.captured_at,
                end=f.captured_at + timedelta(microseconds=f.exposure_us),
                confidence=round(min(1.0, 0.5 + 0.5 * min(1.0, (s.length - 40.0) / 200.0)), 2),
                has_image=has_image,
                data={
                    "length_px": round(s.length),
                    "peak": round(s.peak, 3),
                    "frames": 1,
                    "direction_deg": s.direction_deg,
                    **self._shower(f, s),
                    **self._position(f, s.cx, s.cy),
                },
            )
            self._append(event)
        return event

    def save_lightning(self, hit: LightningHit) -> Event:
        """Store the whole frame (scaled), thumbnail and index line (SPEC §6.4)."""
        f = hit.frame
        with self._lock:
            eid = self._new_id(EventKind.LIGHTNING, f)
            has_image = self._save_full(hit.image_path, f.night_id, eid)
            event = Event(
                id=eid,
                night_id=f.night_id,
                kind=EventKind.LIGHTNING,
                start=f.captured_at,
                end=f.captured_at + timedelta(microseconds=f.exposure_us),
                confidence=round(min(1.0, 0.5 + 25.0 * hit.flash.area_frac), 2),
                has_image=has_image,
                data={
                    "area_frac": hit.flash.area_frac,
                    "peak": hit.flash.peak,
                    "storm_flashes": hit.storm_flashes,
                    "storm": "storm-" + hit.storm_start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
                    **self._position(f, hit.flash.x, hit.flash.y),
                },
            )
            self._append(event)
        return event

    def save_aurora(self, update: AuroraUpdate) -> Event:
        """Store or update an aurora episode (SPEC §6.4)."""
        b = update.episode.best
        return self._save_episode(EventKind.AURORA, update, {"green": b.green},
                                  b.direction_deg, b.x, b.y)  # fmt: skip

    def save_nlc(self, update: NlcUpdate) -> Event:
        """Store or update an NLC episode (SPEC §6.4)."""
        b = update.episode.best
        return self._save_episode(EventKind.NLC, update, {"blue": b.blue},
                                  b.direction_deg, b.x, b.y)  # fmt: skip

    def _save_episode(
        self,
        kind: EventKind,
        update: EpisodeUpdate[Any],
        extra: dict[str, float],
        direction: float | None,
        x: float | None,
        y: float | None,
    ) -> Event:
        """Same id while the episode grows, picture replaced when a better frame comes,
        with `image_rev` counting the pictures."""
        ep = update.episode
        f = ep.start
        with self._lock:
            if ep.event_id is None:
                ep.event_id = self._new_id(kind, f)
                # An episode left open by an agent restart has ended.
                for old in self.night_events(f.night_id):
                    if old.kind is kind and old.data.get("ongoing") is True:
                        self._append(
                            old.model_copy(update={"data": {**old.data, "ongoing": False}})
                        )
            eid = ep.event_id
            fresh = update.picture_changed or ep.image_rev == 0
            if fresh and self._save_full(ep.best_path, f.night_id, eid):
                ep.image_rev += 1
            index = float(ep.best.index)
            event = Event(
                id=eid,
                night_id=f.night_id,
                kind=kind,
                start=f.captured_at,
                end=ep.end,
                confidence=round(min(1.0, 0.5 + index / 10.0), 2),
                has_image=ep.image_rev > 0,
                data={
                    "peak_index": index,
                    **extra,
                    "frames": ep.frames,
                    "direction_deg": direction,
                    "ongoing": ep.ongoing,
                    "image_rev": ep.image_rev,
                    **self._position(ep.best_frame, x, y),
                },
            )
            self._append(event)
        return event

    def _save_full(self, path: Path, night: str, eid: str) -> bool:
        """The whole frame (at most FULL_MAX_PX wide) as the event's picture."""
        try:
            with PILImage.open(path) as im:
                rgb = im.convert("RGB")
        except OSError:
            log.warning("%s: frame %s unreadable", eid, path)
            return False
        if rgb.width > FULL_MAX_PX:
            rgb = rgb.resize((FULL_MAX_PX, round(rgb.height * FULL_MAX_PX / rgb.width)))
        self._write_pictures(rgb, night, eid)
        return True

    def _save_crop(self, hit: MeteorHit, night: str, eid: str) -> bool:
        try:
            with PILImage.open(hit.image_path) as im:
                rgb = im.convert("RGB")
        except OSError:
            log.warning(
                "meteor %s: frame %s unreadable, event without picture", eid, hit.image_path
            )
            return False
        s = hit.streak
        x0, x1 = sorted((s.p1[0], s.p2[0]))
        y0, y1 = sorted((s.p1[1], s.p2[1]))
        side = max(CROP_MIN_PX, round(max(x1 - x0, y1 - y0) * 1.6))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        left = int(min(max(0, cx - side / 2), max(0, rgb.width - side)))
        top = int(min(max(0, cy - side / 2), max(0, rgb.height - side)))
        crop = rgb.crop((left, top, min(rgb.width, left + side), min(rgb.height, top + side)))
        self._write_pictures(crop, night, eid)
        return True

    def _write_pictures(self, pic: PILImage.Image, night: str, eid: str) -> None:
        d = self._dir(night)
        (d / "thumbnails").mkdir(parents=True, exist_ok=True)
        tmp = d / f"{eid}.tmp.jpg"
        pic.save(tmp, format="JPEG", quality=92)
        tmp.replace(d / f"{eid}.jpg")
        h = max(1, round(pic.height * THUMB_WIDTH / pic.width))
        tmp = d / "thumbnails" / f"{eid}.tmp.jpg"
        pic.resize((THUMB_WIDTH, h)).save(tmp, format="JPEG", quality=80)
        tmp.replace(d / "thumbnails" / f"{eid}.jpg")


@dataclass(frozen=True)
class _Job:
    frame: FrameInfo
    image: Image
    path: Path


class DetectionWorker:
    """Runs the detectors beside the capture loop; never blocks it (architecture rule 1)."""

    def __init__(
        self,
        store: ImageStore,
        on_event: Callable[[Event], None] | None = None,
        cfg: MeteorConfig | None = None,
        mask: Mask | None = None,
        mask_radius_frac: float | None = None,
        events: EventStore | None = None,
        lightning_cfg: LightningConfig | None = None,
        sky: SkyMeter | None = None,
        aurora_cfg: AuroraConfig | None = None,
        sky_mask: SkyMask | None = None,
        orienter: Orienter | None = None,
        nlc: NlcDetector | None = None,
        latitude: Callable[[], float | None] | None = None,
    ) -> None:
        self._store = store
        self.events = events or EventStore(store)
        self._on_event = on_event
        self._meteor = MeteorDetector(cfg, mask, mask_radius_frac)
        self._lightning = LightningDetector(lightning_cfg, mask, mask_radius_frac)
        self._sky = sky
        self._aurora = AuroraDetector(
            aurora_cfg,
            mask_radius_frac or 0.48,
            orientation=(lambda: orienter.solution) if orienter is not None else None,
            latitude=latitude,
            sky_mask=sky_mask.get if sky_mask is not None else None,
        )
        self._sky_mask = sky_mask
        self._orienter = orienter
        self._nlc = nlc
        self._mask_version = -1
        self._q: queue.Queue[_Job | None] = queue.Queue(maxsize=4)
        self._dropped = 0
        self._thread = threading.Thread(target=self._run, name="detect", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def on_frame(self, frame: FrameInfo, image: Image) -> None:
        """Called from the capture thread for every stored frame."""
        path = self._store.night_dir(frame.night_id) / frame.name
        try:
            self._q.put_nowait(_Job(frame, image, path))
        except queue.Full:
            self._dropped += 1
            if self._dropped % 10 == 1:
                log.warning("detection behind; %d frame(s) skipped", self._dropped)

    def _emit(self, hits: list[MeteorHit]) -> None:
        for hit in hits:
            event = self.events.save_meteor(hit)
            log.info(
                "meteor %s: %d px, peak %.2f", event.id, round(hit.streak.length), hit.streak.peak
            )
            if self._on_event is not None:
                self._on_event(event)

    def _emit_lightning(self, hits: list[LightningHit]) -> None:
        for hit in hits:
            event = self.events.save_lightning(hit)
            log.info(
                "lightning %s: %.1f %% of the sky, %d in the storm",
                event.id,
                100 * hit.flash.area_frac,
                hit.storm_flashes,
            )
            if self._on_event is not None:
                self._on_event(event)

    def _emit_aurora(self, updates: list[AuroraUpdate]) -> None:
        for u in updates:
            event = self.events.save_aurora(u)
            log.info(
                "aurora %s: index %.1f %%, %d frame(s)%s",
                event.id,
                u.episode.best.index,
                u.episode.frames,
                "" if u.episode.ongoing else ", ended",
            )
            if self._on_event is not None:
                self._on_event(event)

    def _emit_nlc(self, updates: list[NlcUpdate]) -> None:
        for u in updates:
            event = self.events.save_nlc(u)
            log.info("nlc %s: index %.1f %%, %d frame(s)", event.id, u.episode.best.index,
                     u.episode.frames)  # fmt: skip
            if self._on_event is not None:
                self._on_event(event)

    def process_frame(self, frame: FrameInfo, image: Image) -> None:
        """Run the detectors on one frame in the calling thread (tests, replays)."""
        self.process(_Job(frame, image, self._store.night_dir(frame.night_id) / frame.name))

    def process(self, job: _Job) -> None:
        if job.frame.mode is not Mode.NIGHT:
            self._emit(self._meteor.flush())
        rgb = np.asarray(job.image, dtype=np.uint8)
        if self._sky_mask is not None and self._sky_mask.version != self._mask_version:
            learned = self._sky_mask.get(rgb.shape[0], rgb.shape[1])
            if learned is not None:
                self._meteor.set_mask(learned)
                self._lightning.set_mask(learned)
                if self._sky is not None:
                    self._sky.set_mask(learned)
            self._mask_version = self._sky_mask.version
        metrics = self._sky.measure(job.frame, rgb) if self._sky is not None else None
        cloud = metrics.cloud_cover if metrics is not None else None
        if self._orienter is not None:
            self._orienter.consider(job.frame, job.path, metrics)
        gray = to_gray(rgb)
        self._emit(self._meteor.feed(job.frame, gray, job.path))
        self._emit_lightning(self._lightning.feed(job.frame, gray, job.path))
        self._emit_aurora(self._aurora.feed(job.frame, rgb, job.path, cloud))
        if self._nlc is not None:
            self._emit_nlc(self._nlc.feed(job.frame, rgb, job.path))

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                self._emit(self._meteor.flush())
                self._lightning.flush()
                self._emit_aurora(self._aurora.flush())
                if self._nlc is not None:
                    self._emit_nlc(self._nlc.flush())
                return
            try:
                self.process(job)
            except Exception:  # a broken frame must not stop detection
                log.exception("detection failed for %s", job.frame.name)

    def stop(self, timeout: float = 10.0) -> None:
        self._q.put(None)
        self._thread.join(timeout)
