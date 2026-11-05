"""The hub's meteor detection for stations (SharedSky, SPEC §10.3, §10.7).

The camera's detection of one frame and its neighbours (``allskyhub_detect.candidates``)
runs on each station's frames as they come: with a new frame, the two before it make a
triplet, and a meteor found is in the middle one. It is kept as an event with
``trial: true``: until a trial has shown its quality it counts for showers and the night
but is never paired (meteor_pairs.sighting).

The two frames before live on disk, not in memory, under ``<data_dir>/tmp/detect/<id>/``:
each as a gray image of at most ``DETECT_SIDE`` px on its long side (``.npy``), the newer
also as its JPEG (the event's picture when it was the middle one), and ``ring.json`` with
their times and sizes. A station needs a few MB there (not counted in its 500 MB). Memory
is taken only while detecting, at most ``AT_ONCE`` at a time on the hub; when both are
busy a frame is not looked at. Folders of stations silent for an hour go, all of them at
the hub's start.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_detect import Candidate, candidates
from allskyhub_server.devices import station_solve, stations
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, EventRecord

log = logging.getLogger(__name__)

DETECT_SIDE = 2048
MAX_PIXELS = 50_000_000
AT_ONCE = 2
STALE_S = 3600.0
MIN_WIDTH = 1280  # SPEC §10.7: smaller frames serve brightness, clouds and aurora only
_ID = re.compile(r"^[0-9A-Za-z]{26}$")

Gray = np.ndarray[Any, np.dtype[np.uint8]]
Mask = np.ndarray[Any, np.dtype[np.bool_]]


@dataclass(frozen=True)
class Held:
    """A frame in the buffer: its files' stem and what the event of it needs."""

    stem: str
    taken: float  # epoch seconds, mid-exposure
    exposure_s: float
    origin: str  # where its time came from
    width: int  # the full frame
    height: int
    gray_w: int
    gray_h: int
    detections: int = 0  # indi-allsky's own count of streaks on it


@dataclass(frozen=True)
class Found:
    """Meteors in the middle frame ``held``, its JPEG, and their tracks in full pixels."""

    held: Held
    jpeg: bytes
    meteors: list[tuple[list[tuple[float, float]], float]]  # track_px, peak


def scaled_gray(jpeg: bytes) -> tuple[Gray, int, int] | None:
    """The frame in gray with its long side at most ``DETECT_SIDE`` (cv2.INTER_AREA), and the
    full size; None for one over ``MAX_PIXELS`` or unreadable."""
    try:
        with Image.open(io.BytesIO(jpeg)) as im:
            width, height = im.size
            if width * height > MAX_PIXELS:
                return None
            im.draft("L", (DETECT_SIDE, DETECT_SIDE))
            gray = im.convert("L")
            raw = gray.tobytes()
            full: Gray = np.ndarray((gray.height, gray.width), dtype=np.uint8, buffer=raw)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    side = max(full.shape)
    if side > DETECT_SIDE:
        f = DETECT_SIDE / side
        size = (max(1, round(full.shape[1] * f)), max(1, round(full.shape[0] * f)))
        full = np.asarray(cv2.resize(full, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)
    return full.copy(), width, height


def to_full(track: list[tuple[float, float]], held: Held) -> list[tuple[float, float]]:
    """A track in the gray image's pixels back in the full frame's."""
    fx, fy = held.width / held.gray_w, held.height / held.gray_h
    return [(round(x * fx, 1), round(y * fy, 1)) for x, y in track]


def sky_mask(
    shape: tuple[int, int], overlay: Mask | None, orientation: dict[str, Any] | None
) -> Mask | None:
    """Where meteors may be (True): not overlay text (image_url.OverlayMask, True there) and
    above the horizon of the orientation's lens circle (r = a1 + a3 at altitude 0)."""
    h, w = shape
    sky: Mask | None = None
    if overlay is not None:
        resized = cv2.resize(overlay.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
        sky = np.equal(np.asarray(resized, dtype=np.uint8), 0)
    lens: dict[str, Any] = (orientation or {}).get("lens") or {}
    if all(isinstance(lens.get(k), int | float) for k in ("cx", "cy", "a1", "a3", "width")):
        k = w / float(lens["width"])
        ys, xs = np.mgrid[0:h, 0:w]
        radius = (float(lens["a1"]) + float(lens["a3"])) * k
        inside = np.less_equal(
            (xs - float(lens["cx"]) * k) ** 2 + (ys - float(lens["cy"]) * k) ** 2, radius**2
        )
        sky = inside if sky is None else np.logical_and(sky, inside)
    return sky


class Buffer:
    """The last two frames of each station on disk (module docstring)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def folder(self, device_id: str) -> Path:
        if not _ID.match(device_id):
            raise ValueError(device_id)
        return self.root / device_id

    def ring(self, device_id: str) -> list[Held]:
        try:
            raw = json.loads((self.folder(device_id) / "ring.json").read_text())
            return [Held(**item) for item in raw][-2:]
        except (OSError, ValueError, TypeError):
            return []

    def gray(self, device_id: str, held: Held) -> Gray | None:
        """The stored gray image, after checking its header: 2D uint8 of the size in the
        ring; anything else drops the station's buffer (it starts anew)."""
        path = self.folder(device_id) / f"{held.stem}.npy"
        try:
            with path.open("rb") as fh:
                version = np.lib.format.read_magic(fh)
                if version == (1, 0):
                    shape, fortran, dtype = np.lib.format.read_array_header_1_0(fh)
                elif version == (2, 0):
                    shape, fortran, dtype = np.lib.format.read_array_header_2_0(fh)
                else:
                    raise ValueError("npy version")
            if fortran or dtype != np.dtype(np.uint8) or shape != (held.gray_h, held.gray_w):
                raise ValueError("npy header")
            loaded = np.load(path, allow_pickle=False)
            return np.asarray(loaded, dtype=np.uint8)
        except (OSError, ValueError):
            log.warning("detect buffer dropped", extra={"device_id": device_id})
            self.drop(device_id)
            return None

    def jpeg(self, device_id: str, held: Held) -> bytes | None:
        try:
            return (self.folder(device_id) / f"{held.stem}.jpg").read_bytes()
        except OSError:
            return None

    def push(self, device_id: str, gray: Gray, jpeg: bytes, held: Held) -> None:
        """Keep the new frame; the oldest goes, and the JPEG of the one before (only the
        newest can still become the middle of a triplet)."""
        folder = self.folder(device_id)
        folder.mkdir(parents=True, exist_ok=True)
        _atomic(folder / f"{held.stem}.npy", _npy(gray))
        _atomic(folder / f"{held.stem}.jpg", jpeg)
        before = self.ring(device_id)
        kept = [*before[-1:], held]
        _atomic(folder / "ring.json", json.dumps([asdict(h) for h in kept]).encode())
        for old in before[:-1]:
            (folder / f"{old.stem}.npy").unlink(missing_ok=True)
            (folder / f"{old.stem}.jpg").unlink(missing_ok=True)
        for old in before[-1:]:
            (folder / f"{old.stem}.jpg").unlink(missing_ok=True)

    def drop(self, device_id: str) -> None:
        shutil.rmtree(self.folder(device_id), ignore_errors=True)

    def clean(self, now: float | None = None, everything: bool = False) -> int:
        """Folders of stations silent for ``STALE_S`` (all of them at the hub's start)."""
        if not self.root.is_dir():
            return 0
        now = time.time() if now is None else now
        removed = 0
        for folder in self.root.iterdir():
            ring = folder / "ring.json"
            stale = not ring.is_file() or now - ring.stat().st_mtime > STALE_S
            if folder.is_dir() and not folder.is_symlink() and (everything or stale):
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        return removed


def _npy(gray: Gray) -> bytes:
    out = io.BytesIO()
    np.save(out, gray, allow_pickle=False)  # pyright: ignore[reportUnknownMemberType]
    return out.getvalue()


def _atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def in_run(a: Held, b: Held, c: Held, usual_s: float | None) -> bool:
    """SPEC §10.7: one full size of at least ``MIN_WIDTH``, each frame at most twice the
    usual interval after the one before (``usual_s``; else the two gaps within twice)."""
    if not ((a.width, a.height) == (b.width, b.height) == (c.width, c.height)):
        return False
    if c.width < MIN_WIDTH:
        return False
    g1, g2 = b.taken - a.taken, c.taken - b.taken
    if g1 <= 0 or g2 <= 0:
        return False
    if usual_s is not None:
        return max(g1, g2) <= 2 * usual_s
    return max(g1, g2) <= 2 * min(g1, g2)


class Detector:
    """Feed every frame of a station; returns the meteors of the frame before it."""

    def __init__(self, root: Path) -> None:
        self.buffer = Buffer(root)
        self._gate = asyncio.Semaphore(AT_ONCE)

    async def feed(
        self,
        device_id: str,
        jpeg: bytes,
        taken: dt.datetime,
        exposure_s: float,
        origin: str,
        *,
        detections: int = 0,
        usual_s: float | None = None,
        overlay: Mask | None = None,
        orientation: dict[str, Any] | None = None,
    ) -> Found | None:
        got = await asyncio.to_thread(scaled_gray, jpeg)
        if got is None:
            return None
        gray, width, height = got
        held = Held(
            stem=f"{time.time_ns()}", taken=taken.timestamp(), exposure_s=exposure_s,
            origin=origin, width=width, height=height, gray_w=gray.shape[1],
            gray_h=gray.shape[0], detections=detections,
        )  # fmt: skip
        ring = await asyncio.to_thread(self.buffer.ring, device_id)
        found: Found | None = None
        if len(ring) == 2 and in_run(ring[0], ring[1], held, usual_s):
            if self._gate.locked():
                log.info("detection busy, frame skipped", extra={"device_id": device_id})
            else:
                async with self._gate:
                    found = await asyncio.to_thread(
                        self._detect, device_id, ring, gray, overlay, orientation
                    )
        await asyncio.to_thread(self.buffer.push, device_id, gray, jpeg, held)
        return found

    def _detect(
        self,
        device_id: str,
        ring: list[Held],
        gray: Gray,
        overlay: Mask | None,
        orientation: dict[str, Any] | None,
    ) -> Found | None:
        before = self.buffer.gray(device_id, ring[0])
        middle = self.buffer.gray(device_id, ring[1])
        jpeg = self.buffer.jpeg(device_id, ring[1])
        if before is None or middle is None or jpeg is None:
            return None
        mask = sky_mask((gray.shape[0], gray.shape[1]), overlay, orientation)
        found: list[Candidate] = candidates(before, middle, gray, mask)
        if not found:
            return None
        meteors = [(to_full(list(c.track_px), ring[1]), float(c.peak)) for c in found]
        return Found(ring[1], jpeg, meteors)


async def store_found(
    db: AsyncSession, store: ImageStore, device: Device, found: Found, source: str
) -> list[str]:
    """Events of the meteors in the middle frame (SPEC §6.4 with ``track_px``): its time,
    exposure and full picture; directions once the station has an orientation; ``trial``
    keeps them out of pairs (module docstring). Returns the event ids."""
    from allskyhub_server.api import connect  # the event pictures of SPEC §10.2

    held = found.held
    taken = dt.datetime.fromtimestamp(held.taken, dt.UTC)
    half = dt.timedelta(seconds=held.exposure_s / 2)
    night = stations.night_id(taken, stations.longitude(device))
    if await stations.night_full(db, device, night):
        return []
    pic = await asyncio.to_thread(stations.picture, found.jpeg)
    orientation = (device.last_status or {}).get("orientation")
    sol = station_solve.from_orientation(orientation) if orientation else None
    ids: list[str] = []
    for track, peak in found.meteors:
        event_id = await stations.event_id(db, device, "meteor", taken - half)
        await connect.save_pictures(store, device.id, night, event_id, found.jpeg, pic.thumb)
        sky = station_solve.directions(sol, [list(p) for p in track]) if sol else None
        db.add(EventRecord(
            device_id=device.id, event_id=event_id, night_id=night, kind="meteor",
            start=taken - half, end=taken + half, confidence=0.7, has_image=True,
            has_thumb=True, has_original=True, original_w=held.width, original_h=held.height,
            data={
                "source": source, "trial": True, "track_px": [list(p) for p in track],
                "peak": peak, "time_source": held.origin, "time_trusted": False,
                "start_azimuth_deg": None, "start_altitude_deg": None,
                "end_azimuth_deg": None, "end_altitude_deg": None, "track_ordered": False,
            } | (sky or {}),
        ))  # fmt: skip
        await db.flush()
        ids.append(event_id)
    await asyncio.to_thread(stations.cap_storage, store.root, device.id)
    return ids
