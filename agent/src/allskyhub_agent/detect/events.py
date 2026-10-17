"""Detections on disk and in the background (SPEC §6.4): pictures, index, worker thread.

Per night: `events/<id>.jpg` (a crop around the detection), `events/thumbnails/<id>.jpg`
and `events.jsonl` with one `event` body per line.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

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
    angle_between,
    match_shower,
    progressing,
    similar,
    sky_unit,
    to_gray,
)
from allskyhub_agent.detect.nlc import NlcDetector, NlcUpdate
from allskyhub_agent.detect.sky import SkyMeter
from allskyhub_agent.process.skymask import SkyMask
from allskyhub_agent.store.images import THUMB_WIDTH, ImageStore
from allskyhub_protocol import Event, EventKind, FrameInfo, Mode, SkyMetrics, event_id

log = logging.getLogger(__name__)

INDEX = "events.jsonl"
MAX_REJECTS = 300  # SPEC §6.4 Trail verdicts: streaks a night that are not meteors
CROP_MIN_PX = 480  # the picture shows the meteor with some sky around it
FULL_MAX_PX = 1920  # a lightning picture is the whole frame, at most this wide
Window = tuple[int, int, int, int]  # left, top, width, height in the frame's pixels


def original(frame: str, window: Window | None) -> dict[str, str | int | None]:
    """`frame` and `crop_*` (SPEC §6.4 Original frame): where the event's picture lies in
    the stored frame; the window is null when the picture is the whole frame."""
    x, y, w, h = window if window is not None else (None, None, None, None)
    return {"frame": frame, "crop_x": x, "crop_y": y, "crop_w": w, "crop_h": h}


class EventStore:
    def __init__(
        self,
        store: ImageStore,
        orientation: Callable[[], Solution | None] | None = None,
        location: Callable[[], tuple[float, float] | None] | None = None,
        time_trusted: Callable[[], bool] = lambda: True,
    ) -> None:
        self._store = store
        self._time_trusted = time_trusted
        self._lock = threading.Lock()
        # SPEC §4.8: the orientation valid when an event is saved gives its sky position.
        self._orientation = orientation
        self._location = location

    def location(self) -> tuple[float, float] | None:
        """(latitude, longitude) of the camera, None until known."""
        return self._location() if self._location is not None else None

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

    def _track_ends(self, frame: FrameInfo, s: Streak) -> dict[str, float | bool | None]:
        """Both ends of the track in the sky for triangulation (SPEC §6.4, roadmap C4). One
        frame does not show the direction of flight, so the order is arbitrary."""
        if not self._time_trusted():
            # Pairing with other cameras needs the right time (SPEC §6.4): no ends without it.
            return {
                "start_azimuth_deg": None,
                "start_altitude_deg": None,
                "end_azimuth_deg": None,
                "end_altitude_deg": None,
                "track_ordered": False,
            }
        a, b = self._position(frame, *s.p1), self._position(frame, *s.p2)
        return {
            "start_azimuth_deg": a["azimuth_deg"],
            "start_altitude_deg": a["altitude_deg"],
            "end_azimuth_deg": b["azimuth_deg"],
            "end_altitude_deg": b["altitude_deg"],
            "track_ordered": False,
        }

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

    def original_path(self, night: str, eid: str) -> Path | None:
        """The stored frame an event was found in (SPEC §6.4 Original frame), None when the
        event names none or retention removed it."""
        if not (len(night) == 8 and night.isdigit()):
            return None
        event = next((e for e in self.night_events(night) if e.id == eid), None)
        name = event.data.get("frame") if event is not None else None
        if not isinstance(name, str) or "/" in name or name.startswith("."):
            return None
        p = self._store.night_dir(night) / name
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

    def amend(self, event: Event, data: dict[str, float | int | str | bool | None]) -> Event:
        """The event again with more `data` (the newer line counts, like an episode's)."""
        with self._lock:
            out = event.model_copy(update={"data": {**event.data, **data}})
            self._append(out)
        return out

    def save_meteor(self, hit: MeteorHit) -> Event:
        """Crop the frame around the streak, store picture, thumbnail and index line."""
        f, s = hit.frame, hit.streak
        with self._lock:
            eid = self._new_id(EventKind.METEOR, f)
            window = self._save_crop(hit, f.night_id, eid)
            has_image = window is not None
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
                    "trail": hit.trail,  # SPEC §6.4 Trail verdicts
                    **self._shower(f, s),
                    **self._position(f, s.cx, s.cy),
                    **self._track_ends(f, s),
                    **(original(f.name, window) if window is not None else {}),
                },
            )
            self._append(event)
        return event

    def extend_pass(self, event: Event, hit: MeteorHit) -> Event:
        """A further piece of a moving object (SPEC §6.4 Trail verdicts): the same event,
        later `end`, one more frame, and `end_*` the new piece's end farther from the start."""
        f, s = hit.frame, hit.streak
        data = dict(event.data)
        data["frames"] = int(cast("int", data.get("frames") or 1)) + 1
        ends = self._track_ends(f, s)
        a1, z1 = data.get("start_altitude_deg"), data.get("start_azimuth_deg")
        if isinstance(a1, float | int) and isinstance(z1, float | int):
            start = sky_unit(float(z1), float(a1))
            far: tuple[float, float] | None = None
            best = -1.0
            for k in ("start", "end"):
                alt, az = ends[f"{k}_altitude_deg"], ends[f"{k}_azimuth_deg"]
                if isinstance(alt, float | int) and isinstance(az, float | int):
                    d = angle_between(start, sky_unit(float(az), float(alt)))
                    if d > best:
                        best, far = d, (float(alt), float(az))
            if far is not None:
                data["end_altitude_deg"], data["end_azimuth_deg"] = far
        end = f.captured_at + timedelta(microseconds=f.exposure_us)
        with self._lock:
            out = event.model_copy(update={"end": max(event.end, end), "data": data})
            self._append(out)
        return out

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
                    **(original(f.name, None) if has_image else {}),
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
                ep.picture_frame = ep.best_frame.name
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
                    **(original(ep.picture_frame, None) if ep.picture_frame else {}),
                },
            )
            self._append(event)
        return event

    def import_event(
        self,
        kind: EventKind,
        night: str,
        start: datetime,
        end: datetime,
        confidence: float,
        data: dict[str, float | int | str | bool | None],
        picture: Path | None,
    ) -> Event | None:
        """An event from another program's records (roadmap L18); None if an event of this
        kind already starts at that second, so a second import adds nothing."""
        with self._lock:
            eid = event_id(kind, start)
            if eid in {e.id for e in self.night_events(night)}:
                return None
            has_image = False
            if picture is not None:
                try:
                    with PILImage.open(picture) as im:
                        self._write_pictures(im.convert("RGB"), night, eid)
                    has_image = True
                except OSError:
                    log.warning("%s: picture %s unreadable", eid, picture)
            event = Event(
                id=eid,
                night_id=night,
                kind=kind,
                start=start,
                end=end,
                confidence=confidence,
                has_image=has_image,
                imported=True,
                data=data,
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

    def _save_crop(self, hit: MeteorHit, night: str, eid: str) -> Window | None:
        """The picture around the streak; its window in the frame, None if unreadable."""
        try:
            with PILImage.open(hit.image_path) as im:
                rgb = im.convert("RGB")
        except OSError:
            log.warning(
                "meteor %s: frame %s unreadable, event without picture", eid, hit.image_path
            )
            return None
        s = hit.streak
        x0, x1 = sorted((s.p1[0], s.p2[0]))
        y0, y1 = sorted((s.p1[1], s.p2[1]))
        side = max(CROP_MIN_PX, round(max(x1 - x0, y1 - y0) * 1.6))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        left = int(min(max(0, cx - side / 2), max(0, rgb.width - side)))
        top = int(min(max(0, cy - side / 2), max(0, rgb.height - side)))
        right, bottom = min(rgb.width, left + side), min(rgb.height, top + side)
        self._write_pictures(rgb.crop((left, top, right, bottom)), night, eid)
        return left, top, right - left, bottom - top

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


# SPEC §6.4: the next piece of a moving object comes within this time (one frame may be
# missing), and one event spans at most PASS_MAX: separate objects along one airway or
# orbit must not grow into one event that no catalogue object fits (Benjamin, 2026-10-15).
PASS_GAP = timedelta(seconds=150)
PASS_MAX = timedelta(minutes=10)
PASS_AXIS_DEG = 25.0  # a continuing piece moved along its own trail ...


@dataclass
class OpenPass:
    event: Event
    streak: Streak
    last: datetime
    step: tuple[float, float] | None = None  # ... and onward, not back (px of the last move)


def onward(p: OpenPass, s: Streak, cfg: MeteorConfig) -> tuple[float, float] | None:
    """The move from the pass's last piece to `s` if `s` continues it (SPEC §6.4): the same
    place (a drifting contrail), or along the trail and in the direction it has been
    moving; None otherwise."""
    if similar(p.streak, s, cfg):
        return (0.0, 0.0)
    if not progressing(p.streak, s, cfg):
        return None
    dx, dy = s.cx - p.streak.cx, s.cy - p.streak.cy
    along = math.degrees(math.atan2(dy, dx)) % 180.0
    d = abs(along - p.streak.angle_deg) % 180.0
    if min(d, 180.0 - d) > PASS_AXIS_DEG:
        return None
    if p.step is not None and p.step != (0.0, 0.0) and dx * p.step[0] + dy * p.step[1] <= 0:
        return None
    return (dx, dy)


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
        enabled: Mapping[str, bool] | None = None,
        on_sky: Callable[[SkyMetrics], None] | None = None,
        on_light_source: Callable[[Event, bool], None] | None = None,
    ) -> None:
        self._store = store
        self._on_sky = on_sky  # the Alpaca safety monitor (SPEC §7.5) sees every measurement
        # Detectors switched off in the settings (roadmap L8) never see a frame.
        self._on = dict.fromkeys(("meteor", "lightning", "aurora"), True) | dict(enabled or {})
        self.events = events or EventStore(store)
        self._on_event = on_event
        self._meteor = MeteorDetector(
            cfg,
            mask,
            mask_radius_frac,
            orientation=(lambda: orienter.solution) if orienter is not None else None,
            location=self.events.location,
        )
        self._lightning = LightningDetector(lightning_cfg, mask, mask_radius_frac)
        # SPEC §6.4 Light source check: flashes by their number, so an earlier one can be
        # marked once a later one shows it was a lamp; only the current storm matters.
        self._on_light_source = on_light_source
        self._flash_events: dict[int, Event] = {}
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
        self._rejects: dict[str, int] = {}  # streaks per night that are not meteors
        self._passes: list[OpenPass] = []  # moving objects still crossing the sky
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

    def _continued(self, hit: MeteorHit) -> Event | None:
        """The open pass this piece of a moving object continues, if any (SPEC §6.4)."""
        if hit.trail not in ("moving", "circle"):
            return None
        cfg = self._meteor.config
        at = hit.frame.captured_at
        self._passes = [
            p for p in self._passes if at - p.last <= PASS_GAP and at - p.event.start < PASS_MAX
        ]
        near: list[tuple[OpenPass, tuple[float, float]]] = []
        for p in self._passes:
            step = onward(p, hit.streak, cfg) if p.event.night_id == hit.frame.night_id else None
            if step is not None:
                near.append((p, step))
        if not near:
            return None
        p, step = min(near, key=lambda q: math.hypot(q[0].streak.cx - hit.streak.cx,
                                                     q[0].streak.cy - hit.streak.cy))  # fmt: skip
        p.event = self.events.extend_pass(p.event, hit)
        p.streak, p.last, p.step = hit.streak, at, step
        return p.event

    def _emit(self, hits: list[MeteorHit]) -> None:
        for hit in hits:
            grown = self._continued(hit)
            if grown is not None:
                log.debug("moving object %s: %d frames", grown.id, grown.data.get("frames"))
                if self._on_event is not None:
                    self._on_event(grown)
                continue
            if hit.trail != "meteor" and not self._room_for_reject(hit.frame.night_id):
                log.info("not a meteor (%s), not kept: %d a night at most", hit.trail, MAX_REJECTS)
                continue
            event = self.events.save_meteor(hit)
            log.info(
                "%s %s: %d px, peak %.2f",
                "meteor" if hit.trail == "meteor" else f"not a meteor ({hit.trail})",
                event.id,
                round(hit.streak.length),
                hit.streak.peak,
            )
            if hit.trail in ("moving", "circle"):
                self._passes.append(OpenPass(event, hit.streak, hit.frame.captured_at))
            if self._on_event is not None:
                self._on_event(event)

    def _room_for_reject(self, night: str) -> bool:
        """SPEC §6.4: at most 300 streaks a night that are not meteors are kept."""
        if night not in self._rejects:
            self._rejects = {
                night: sum(
                    1
                    for e in self.events.night_events(night)
                    if e.kind is EventKind.METEOR and e.data.get("trail", "meteor") != "meteor"
                )
            }
        if self._rejects[night] >= MAX_REJECTS:
            return False
        self._rejects[night] += 1
        return True

    def _emit_lightning(self, hits: list[LightningHit]) -> None:
        for hit in hits:
            event = self.events.save_lightning(hit)
            log.info(
                "lightning %s: %.1f %% of the sky, %d in the storm%s",
                event.id,
                100 * hit.flash.area_frac,
                hit.storm_flashes,
                ", a light source nearby" if hit.light_source else "",
            )
            # The label of this flash is set before it goes out (false positives count
            # nowhere, SPEC §6.4); earlier flashes found to be the lamp only now, after.
            if self._on_light_source is not None and hit.light_source:
                self._on_light_source(event, False)
            if self._on_event is not None:
                self._on_event(event)
            self._flash_events[hit.seq] = event
            for old in [k for k in self._flash_events if k < hit.seq - 500]:
                del self._flash_events[old]
            if self._on_light_source is not None:
                for e in [self._flash_events.get(s) for s in hit.also]:
                    if e is not None:
                        self._on_light_source(e, True)

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
        if metrics is not None and (metrics.cloud_cover, metrics.sqm_mag) != (None, None):
            self._store.append_sky(metrics)
        if metrics is not None and self._on_sky is not None:
            self._on_sky(metrics)
        cloud = metrics.cloud_cover if metrics is not None else None
        if self._orienter is not None:
            self._orienter.consider(job.frame, job.path, metrics)
        gray = to_gray(rgb)
        if self._on["meteor"]:
            self._emit(self._meteor.feed(job.frame, gray, job.path))
        if self._on["lightning"]:
            self._emit_lightning(self._lightning.feed(job.frame, gray, job.path, rgb))
        if self._on["aurora"]:
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
