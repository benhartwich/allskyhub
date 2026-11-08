"""Fireballs sent later (SPEC §10.8): an owner sends an older frame of a fireball, the hub
checks it against the stars and, once it passes, keeps it as a meteor of the camera.

The check solves the frame with the camera's own solver (``allskyhub_solve``, as
devices/station_solve.py) at the owner's time and the camera's place. Near the zenith the
solution that puts the sky within ``MAX_OFF_DEG`` of the camera's current orientation is
the same camera at the same place, with the sidereal time right to about two minutes.
The stars cannot tell the date: the same frame a day later and 3 min 56 s earlier passes
too. Date and seconds are the owner's word; pairing takes them as a live camera's (§6.4),
and a wrong one simply finds no other camera.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import math
import uuid
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from allskyhub_protocol import EventVariant
from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import fireball, meteor_pairs, station_solve, stations, stats
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, EventRecord, LateFireball, MeteorPair
from allskyhub_sky.astro import sun_altitude
from allskyhub_solve import Solution, solve_verdict

log = logging.getLogger(__name__)

MAX_AGE = dt.timedelta(days=365)
PER_DAY = 20
UPLOADS_PER_DAY = (ratelimit.Limit(PER_DAY, 86400),)
# Each check is a full solve on the hub; another time may be tried a few times per upload.
CHECKS_PER_DAY = (ratelimit.Limit(60, 86400),)
MAX_BYTES = 8 * 1024**2
MAX_EXPOSURE_S = 120.0
MIN_TRACK, MAX_TRACK = 2, 32
DARK_SUN_DEG = -18.0
MAX_OFF_DEG = 0.5
MIN_TRACK_DEG = 15.0
# A failed one stays for its owner this long (maintenance.py).
KEEP_FAILED = dt.timedelta(days=30)

# Why a check failed; the pages say each in words (web/routes_late.py).
REASONS = (
    "no_orientation",  # the camera has no orientation (yet) to compare with
    "size",  # the camera's frame has another size by now
    "daylight",  # the sun was above DARK_SUN_DEG at the given time
    "few_stars",  # too few stars: cloudy, or not a night frame
    "no_match",  # enough stars, no solution: wrong time or place
    "turned",  # solved, but the sky lies elsewhere: wrong time or a moved camera
    "mirrored",  # solved, mirrored the other way than the camera
    "track",  # the track leaves the sky
    "not_fireball",  # neither bright nor long enough
    "known",  # the camera has a meteor at that time already
)


class LateError(Exception):
    """An upload refused at once; ``what`` names it for the page."""

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


def as_jpeg(data: bytes) -> bytes:
    """The frame as stored: a JPEG as sent, a PNG converted at high quality (stars are
    single pixels). ``LateError("image")`` for anything else."""
    if len(data) > MAX_BYTES:
        raise LateError("too_large")
    if data.startswith(b"\xff\xd8\xff"):
        return data
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "PNG" or im.size[0] * im.size[1] > stations.MAX_PIXELS:
                raise LateError("image")
            out = io.BytesIO()
            im.convert("RGB").save(out, "JPEG", quality=95)
            return out.getvalue()
    except LateError:
        raise
    except Exception as exc:  # Pillow raises many kinds for broken files
        raise LateError("image") from exc


def image_size(data: bytes) -> tuple[int, int]:
    try:
        with stations.open_jpeg(data) as im:
            return im.size
    except stations.StationError as exc:
        raise LateError("image") from exc


def frame_size(device: Device) -> tuple[int, int] | None:
    """The size the camera's orientation was solved in: the frame an upload must have."""
    found = (device.last_status or {}).get("orientation")
    sol = station_solve.from_orientation(found) if found else None
    if sol is None or not sol.width or not sol.height:
        return None
    return sol.width, sol.height


async def count(engine: AsyncEngine, device: Device, what: str) -> None:
    """One upload (``"upload"``) or check (``"check"``) of the owner's; ``LateError`` past
    the day's limit. Counted apart from the rows, so deleting one frees nothing."""
    limits = UPLOADS_PER_DAY if what == "upload" else CHECKS_PER_DAY
    try:
        await ratelimit.hit(engine, f"late-{what}:{device.owner_id}", limits)
    except ratelimit.RateLimitedError as exc:
        raise LateError("today" if what == "upload" else "checks_today") from exc


def check_time(taken: dt.datetime, exposure_s: float, now: dt.datetime) -> None:
    if not 0 < exposure_s <= MAX_EXPOSURE_S:
        raise LateError("exposure")
    if taken > now or now - taken > MAX_AGE:
        raise LateError("age")


def check_track(track: list[list[float]], width: int, height: int) -> list[list[float]]:
    if not MIN_TRACK <= len(track) <= MAX_TRACK:
        raise LateError("track")
    out: list[list[float]] = []
    for point in track:
        if len(point) != 2:
            raise LateError("track")
        x, y = float(point[0]), float(point[1])
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= width and 0 <= y <= height):
            raise LateError("track")
        out.append([round(x, 1), round(y, 1)])
    return out


def compare(found: Solution, known: Solution) -> tuple[str | None, float | None]:
    """SPEC §10.8: the same camera at the same place, the sidereal time right to minutes;
    the reason it is not, and the offset found."""
    if (found.flip < 0) != (known.flip < 0):
        return "mirrored", None
    off = station_solve.offset_deg(found, known)
    if off is None or off > MAX_OFF_DEG:
        return "turned", off
    return None, off


def _separation(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Degrees between two (azimuth, altitude) directions."""
    az1, alt1, az2, alt2 = (math.radians(v) for v in (*a, *b))
    c = math.sin(alt1) * math.sin(alt2) + math.cos(alt1) * math.cos(alt2) * math.cos(az1 - az2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def is_fireball(peak: float, sky: dict[str, Any]) -> bool:
    ends = (
        (float(sky["start_azimuth_deg"]), float(sky["start_altitude_deg"])),
        (float(sky["end_azimuth_deg"]), float(sky["end_altitude_deg"])),
    )
    return peak >= fireball.FIREBALL_PEAK or _separation(*ends) >= MIN_TRACK_DEG


def masked(data: bytes, track: list[list[float]]) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """The frame in gray with the fireball painted over in the sky's median, so that its
    fragments are not taken for stars (allsky's review, 2026-11-08)."""
    with stations.open_jpeg(data) as im:
        gray = im.convert("L")
    sky = stations.median(gray.resize((64, 64)).histogram())
    width = max(8, round(gray.width / 300))
    ImageDraw.Draw(gray).line([(p[0], p[1]) for p in track], fill=sky, width=width, joint="curve")
    raw = gray.tobytes()
    # np.ndarray over the bytes, not np.frombuffer (numpy 2.5 typing, CLAUDE.md).
    return np.ndarray((gray.height, gray.width), dtype=np.uint8, buffer=raw).copy()


_one_at_a_time = asyncio.Lock()
# Running checks, kept so they are not collected (and for tests to wait on).
TASKS: set[asyncio.Task[None]] = set()


def start(maker: async_sessionmaker[AsyncSession], store: ImageStore, upload_id: uuid.UUID) -> None:
    """Check in the background, never in the request."""
    task = asyncio.get_running_loop().create_task(check(maker, store, upload_id))
    task.add_done_callback(TASKS.discard)
    TASKS.add(task)


async def resume(maker: async_sessionmaker[AsyncSession], store: ImageStore) -> int:
    """At the hub's start: the checks a restart ended start again (allsky's review,
    2026-11-08). Returns how many."""
    async with maker() as db:
        waiting = (
            await db.scalars(select(LateFireball.id).where(LateFireball.state == "checking"))
        ).all()
    for upload_id in waiting:
        start(maker, store, upload_id)
    return len(waiting)


async def _fail(
    maker: async_sessionmaker[AsyncSession],
    upload_id: uuid.UUID,
    reason: str,
    found: dict[str, Any] | None = None,
) -> None:
    async with maker() as db:
        upload = await db.get(LateFireball, upload_id)
        if upload is not None:
            upload.state, upload.reason = "failed", reason
            upload.check = found or {}
            await db.commit()


async def check(
    maker: async_sessionmaker[AsyncSession], store: ImageStore, upload_id: uuid.UUID
) -> None:
    """SPEC §10.8 for one upload; it ends ``accepted`` with its event, or ``failed``."""
    try:
        await _check(maker, store, upload_id)
    except Exception:  # a bug in a check must not end the hub's task
        log.exception("late fireball check failed", extra={"upload": str(upload_id)})
        await _fail(maker, upload_id, "no_match")


async def _check(
    maker: async_sessionmaker[AsyncSession], store: ImageStore, upload_id: uuid.UUID
) -> None:
    async with maker() as db:
        upload = await db.get(LateFireball, upload_id)
        device = await db.get(Device, upload.device_id) if upload else None
        if upload is None or device is None or upload.state != "checking":
            return
        found = (device.last_status or {}).get("orientation")
        known = station_solve.from_orientation(found) if found else None
        place = station_solve._place(device)  # pyright: ignore[reportPrivateUsage]
        taken, track = upload.taken_at, upload.track_px
        size = (upload.width, upload.height)
    if known is None or place is None:
        await _fail(maker, upload_id, "no_orientation")
        return
    if (known.width, known.height) != size:
        await _fail(maker, upload_id, "size")
        return
    if sun_altitude(taken, *place) > DARK_SUN_DEG:
        await _fail(maker, upload_id, "daylight")
        return
    path = store.late_path(device.id, str(upload_id))
    data = await asyncio.to_thread(path.read_bytes)
    async with _one_at_a_time:
        gray = await asyncio.to_thread(masked, data, track)
        sol, verdict = await asyncio.to_thread(solve_verdict, gray, taken, *place)
    if sol is None:
        reason = "few_stars" if verdict.reason == "few stars" else "no_match"
        await _fail(maker, upload_id, reason)
        return
    off, offset = compare(sol, known)
    result: dict[str, Any] = {
        "stars": sol.stars,
        "rms_deg": sol.rms_deg,
        "offset_deg": None if offset is None else round(offset, 2),
    }
    if off is not None:
        await _fail(maker, upload_id, off, result)
        return
    sky = station_solve.directions(sol, track)
    if sky is None:
        await _fail(maker, upload_id, "track", result)
        return
    peak = await asyncio.to_thread(stations.track_peak, data, [(p[0], p[1]) for p in track])
    result["peak"] = round(peak, 2)
    if not is_fireball(peak, sky):
        await _fail(maker, upload_id, "not_fireball", result)
        return
    event_id = await _keep(maker, store, upload_id, data, sky, peak, result)
    if event_id is None:
        return
    # meteor_pairs marks its pairs as sent later, now and when paired again (§10.8).
    await meteor_pairs.pair_event(maker, device.id, event_id)


async def _keep(
    maker: async_sessionmaker[AsyncSession],
    store: ImageStore,
    upload_id: uuid.UUID,
    data: bytes,
    sky: dict[str, Any],
    peak: float,
    result: dict[str, Any],
) -> str | None:
    """The accepted fireball as a meteor event of §6.4, ``imported``; its pictures move
    to the event's."""
    async with maker() as db:
        upload = await db.get(LateFireball, upload_id)
        device = await db.get(Device, upload.device_id) if upload else None
        if upload is None or device is None:
            return None
        half = dt.timedelta(seconds=upload.exposure_s / 2)
        start, end = upload.taken_at - half, upload.taken_at + half
        known = await db.scalar(
            select(EventRecord.event_id).where(
                EventRecord.device_id == device.id,
                EventRecord.kind == "meteor",
                EventRecord.start <= end,
                EventRecord.end >= start,
            )
        )
        if known is not None:
            upload.state, upload.reason, upload.check = "failed", "known", result
            await db.commit()
            return None
        pic = await asyncio.to_thread(stations.picture, data)
        night = stations.night_id(start, stations.longitude(device))
        event_id = await stations.event_id(db, device, "meteor", start)
        for variant, picture in ((EventVariant.ORIGINAL, data), (EventVariant.THUMB, pic.thumb)):
            await asyncio.to_thread(store.save_event, device.id, night, event_id, variant, picture)
        db.add(EventRecord(
            device_id=device.id, event_id=event_id, night_id=night, kind="meteor",
            start=start, end=end, confidence=1.0, has_image=True, has_thumb=True,
            has_original=True, original_w=pic.width, original_h=pic.height,
            data={
                "source": "late", "imported": True, "track_px": upload.track_px,
                "peak": peak, "track_ordered": False, "time_trusted": True,
            } | sky,
        ))  # fmt: skip
        upload.state, upload.reason, upload.check = "accepted", None, result
        upload.event_id = event_id
        await db.commit()
    await asyncio.to_thread(store.delete_late, device.id, str(upload_id))
    if device.kind == "station":
        await asyncio.to_thread(stations.cap_storage, store.root, device.id)
    return event_id


def _of_event(device_id: str, event_id: str) -> Any:
    return or_(
        (MeteorPair.device_a == device_id) & (MeteorPair.event_a == event_id),
        (MeteorPair.device_b == device_id) & (MeteorPair.event_b == event_id),
    )


async def remove(db: AsyncSession, store: ImageStore, upload: LateFireball) -> None:
    """The owner deletes one (SPEC §10.8): its event, pictures and every pair it made."""
    if upload.event_id is not None:
        event = await db.scalar(
            select(EventRecord).where(
                EventRecord.device_id == upload.device_id, EventRecord.event_id == upload.event_id
            )
        )
        await db.execute(delete(MeteorPair).where(_of_event(upload.device_id, upload.event_id)))
        if event is not None:
            for variant in EventVariant:
                await asyncio.to_thread(
                    store.delete_event, upload.device_id, event.night_id, event.event_id, variant
                )
            await db.delete(event)
    await asyncio.to_thread(store.delete_late, upload.device_id, str(upload.id))
    await db.delete(upload)


async def hide(db: AsyncSession, upload: LateFireball) -> None:
    """The admin hides an accepted one: it counts nowhere and its pairs go."""
    if upload.event_id is None:
        return
    await db.execute(delete(MeteorPair).where(_of_event(upload.device_id, upload.event_id)))
    event = await db.scalar(
        select(EventRecord).where(
            EventRecord.device_id == upload.device_id, EventRecord.event_id == upload.event_id
        )
    )
    if event is not None:
        # labels.set_on_hub refuses to change it back (``hidden``).
        event.label, event.label_reason = "false_positive", "other"
        event.labelled_at, event.label_auto = dt.datetime.now(dt.UTC), False
        event.data = {**(event.data or {}), "hidden": True}
        await stats.refresh_night(db, upload.device_id, event.night_id)
    upload.check = {**upload.check, "hidden": True}


async def purge(db: AsyncSession, store: ImageStore, now: dt.datetime) -> None:
    """Uploads that never passed go after ``KEEP_FAILED`` (maintenance.py), a check that
    never ended too."""
    old = await db.scalars(
        select(LateFireball).where(
            LateFireball.state.in_(("track", "checking", "failed")),
            LateFireball.created_at < now - KEEP_FAILED,
        )
    )
    for upload in old.all():
        await remove(db, store, upload)
