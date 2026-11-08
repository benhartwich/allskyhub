"""The hub solves a station's orientation itself (SharedSky, SPEC §10.1, §4.8).

A station without an orientation, or with one older than ``RESOLVE_AFTER``, is asked for a
full frame (``want_full_frame``) while the sun is ``DARK_SUN_DEG`` or lower at its place,
at most ``TRIES_PER_NIGHT`` times a night. A full frame is solved in the background, one at
a time on the whole hub (it is decoded in full: stars are single pixels), with the
camera's own solver and acceptance (``allskyhub_solve``). An accepted solution becomes the
station's ``orientation`` in the form of SPEC §6.3, so pairing and overlays read it as for
cameras; then the station's meteors of the last ``RESOLVE_AFTER`` get their directions from
their pixel tracks and are paired, if their time was trusted.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import Lens, Orientation
from allskyhub_server.devices import meteor_pairs, stations
from allskyhub_server.models import Device, EventRecord
from allskyhub_sky.astro import sun_altitude
from allskyhub_solve import Solution, solve_verdict

log = logging.getLogger(__name__)

RESOLVE_AFTER = dt.timedelta(days=7)
DARK_SUN_DEG = -18.0
TRIES_PER_NIGHT = 3

_one_at_a_time = asyncio.Lock()
# Running solves, kept so they are not collected (and for tests to wait on).
TASKS: set[asyncio.Task[bool]] = set()


def _place(device: Device) -> tuple[float, float] | None:
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if isinstance(lat, int | float) and isinstance(lon, int | float):
        return float(lat), float(lon)
    return None


def _solve_state(device: Device) -> dict[str, Any]:
    return dict((device.last_status or {}).get("solve") or {})


def orientation_fresh(device: Device, now: dt.datetime) -> bool:
    found: dict[str, Any] | None = (device.last_status or {}).get("orientation")
    if not found:
        return False
    try:
        solved = dt.datetime.fromisoformat(str(found.get("solved_at")))
    except ValueError:
        return False
    return now - solved < RESOLVE_AFTER


def want_full_frame(device: Device, now: dt.datetime) -> bool:
    """SPEC §10.1: no fresh orientation, a dark sky at the station and tries left tonight."""
    place = _place(device)
    if place is None or orientation_fresh(device, now):
        return False
    if sun_altitude(now, *place) > DARK_SUN_DEG:
        return False
    state = _solve_state(device)
    night = stations.night_id(now, place[1])
    return not (state.get("night") == night and int(state.get("tries", 0)) >= TRIES_PER_NIGHT)


def count_try(device: Device, now: dt.datetime) -> None:
    """A full frame arrived for solving: one try of tonight's."""
    place = _place(device)
    night = stations.night_id(now, place[1] if place else None)
    state = _solve_state(device)
    tries = int(state.get("tries", 0)) if state.get("night") == night else 0
    status = dict(device.last_status or {})
    status["solve"] = state | {"night": night, "tries": tries + 1}
    device.last_status = status


def _gray(data: bytes) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """The full frame as a gray image at full resolution (the 50 MP cap holds: the size is
    checked from the header before decoding, stations._open)."""
    with stations.open_jpeg(data) as im:
        gray = im.convert("L")
        raw = gray.tobytes()
        # np.ndarray over the bytes, not np.frombuffer (numpy 2.5 typing, CLAUDE.md).
        return np.ndarray((gray.height, gray.width), dtype=np.uint8, buffer=raw).copy()


def to_orientation(sol: Solution, at: dt.datetime) -> dict[str, Any]:
    """SPEC §6.3 ``orientation``, as the agent reports it (calib/orientation.py)."""
    return Orientation(
        north_deg=sol.rot % 360.0,
        mirrored=sol.flip < 0,
        solved_at=at,
        stars=sol.stars,
        rms_deg=sol.rms_deg,
        lens=Lens(
            cx=sol.cx,
            cy=sol.cy,
            a1=sol.a1,
            a3=sol.a3,
            tilt_east_deg=sol.tilt_east,
            tilt_north_deg=sol.tilt_north,
            width=sol.width,
            height=sol.height,
        ),
    ).model_dump(mode="json")


def from_orientation(found: dict[str, Any]) -> Solution | None:
    """The solver's lens again, to turn pixels into directions."""
    try:
        o = Orientation.model_validate(found)
    except ValueError:
        return None
    if o.lens is None:
        return None
    return Solution(
        cx=o.lens.cx, cy=o.lens.cy, a1=o.lens.a1, a3=o.lens.a3, rot=o.north_deg,
        flip=-1.0 if o.mirrored else 1.0, tilt_east=o.lens.tilt_east_deg,
        tilt_north=o.lens.tilt_north_deg, stars=o.stars, rms_px=0.0, rms_deg=o.rms_deg,
        width=o.lens.width, height=o.lens.height,
    )  # fmt: skip


MIN_COMPARE_ALT = 30.0  # the sky compared: from this altitude up


def offset_deg(found: Solution, known: Solution) -> float | None:
    """The largest angle between where both solutions put the same pixel, over the sky
    from ``MIN_COMPARE_ALT`` up: a wrong time turns the sky rigidly about the pole, which
    the fit takes as another lie of the camera, shared between north, tilt and centre
    (allsky's review, 2026-11-08); the directions show it as about 15°/h at any latitude.
    None without such sky."""
    w, h = known.width, known.height
    xs = [w * (i + 0.5) / 40 for i in range(40)]
    ys = [h * (j + 0.5) / 30 for j in range(30)]
    x = np.array([v for _ in ys for v in xs], dtype=np.float64)
    y = np.array([v for v in ys for _ in xs], dtype=np.float64)
    alt1, az1 = known.unproject(x, y)
    alt2, az2 = found.unproject(x, y)
    a1, z1, a2, z2 = (np.radians(v) for v in (alt1, az1, alt2, az2))
    cos = np.sin(a1) * np.sin(a2) + np.cos(a1) * np.cos(a2) * np.cos(z1 - z2)
    angle = np.degrees(np.arccos(np.minimum(1.0, np.maximum(-1.0, cos))))
    keep = np.isfinite(angle) & np.isfinite(alt1) & (alt1 >= MIN_COMPARE_ALT)
    if not bool(keep.any()):
        return None
    return float(angle[keep].max())


def directions(sol: Solution, track_px: list[list[float]]) -> dict[str, Any] | None:
    """SPEC §6.4 from a pixel track: both ends and ``track`` as [azimuth, altitude]."""
    points = [sol.position(float(x), float(y), sol.width, sol.height) for x, y in track_px]
    if len(points) < 2 or any(p is None for p in points):
        return None
    sky = [p for p in points if p is not None]
    return {
        "start_azimuth_deg": sky[0][0], "start_altitude_deg": sky[0][1],
        "end_azimuth_deg": sky[-1][0], "end_altitude_deg": sky[-1][1],
        "track": [[az, alt] for az, alt in sky],
    }  # fmt: skip


async def apply(
    maker: async_sessionmaker[AsyncSession], device_id: str, now: dt.datetime
) -> list[str]:
    """Directions for the station's meteors of the last ``RESOLVE_AFTER`` from its
    orientation; pairs those whose time was trusted. Returns the events changed."""
    changed: list[str] = []
    async with maker() as db:
        device = await db.get(Device, device_id)
        found = (device.last_status or {}).get("orientation") if device else None
        sol = from_orientation(found) if found else None
        if device is None or sol is None:
            return []
        rows = (
            await db.scalars(
                select(EventRecord).where(
                    EventRecord.device_id == device_id,
                    EventRecord.kind == "meteor",
                    EventRecord.start >= now - RESOLVE_AFTER,
                )
            )
        ).all()
        trusted: list[str] = []
        for event in rows:
            data = dict(event.data or {})
            track = data.get("track_px")
            if not isinstance(track, list) or len(track) < 2:  # pyright: ignore[reportUnknownArgumentType]
                continue
            sky = directions(sol, track)  # pyright: ignore[reportUnknownArgumentType]
            if sky is None:
                continue
            event.data = data | sky
            changed.append(event.event_id)
            if data.get("time_trusted"):
                trusted.append(event.event_id)
        await db.commit()
    for event_id in trusted:
        await meteor_pairs.pair_event(maker, device_id, event_id)
    return changed


async def solve(
    maker: async_sessionmaker[AsyncSession], device_id: str, data: bytes, taken: dt.datetime
) -> bool:
    """Solve one full frame of a station (taken at ``taken``, mid-exposure); True when
    accepted. One at a time on the hub."""
    async with maker() as db:
        device = await db.get(Device, device_id)
        place = _place(device) if device else None
    if place is None:
        return False
    async with _one_at_a_time:
        try:
            gray = await asyncio.to_thread(_gray, data)
            sol, verdict = await asyncio.to_thread(solve_verdict, gray, taken, *place)
        except stations.StationError:
            return False
        except Exception:  # a bug in a solve must not end the hub's task
            log.exception("station solve failed", extra={"device_id": device_id})
            return False
    if sol is None or not verdict.ok:
        log.info("station not solved", extra={"device_id": device_id, "reason": verdict.reason})
        if verdict.reason in FAILED_ON_CLEAR:
            await _failed_night(maker, device_id, taken)
        return False
    async with maker() as db:
        device = await db.get(Device, device_id)
        if device is None:
            return False
        info = dict((device.last_status or {}).get("station") or {})
        if (info.get("width"), info.get("height")) != (sol.width, sol.height):
            return False  # the station changed its frame size meanwhile
        status = dict(device.last_status or {})
        before = from_orientation(status["orientation"]) if status.get("orientation") else None
        if before is not None and (before.width, before.height) == (sol.width, sol.height):
            # How far two good solutions of one camera lie apart: the spread the check of
            # fireballs sent later (§10.8, MAX_OFF_DEG) has to allow (allsky, 2026-11-08).
            log.info(
                "station solved again",
                extra={"device_id": device_id, "offset_deg": offset_deg(sol, before)},
            )
        status["orientation"] = to_orientation(sol, dt.datetime.now(dt.UTC))
        status["solve"] = {**dict(status.get("solve") or {}), "fails": []}
        device.last_status = status
        await db.commit()
    await apply(maker, device_id, dt.datetime.now(dt.UTC))
    return True


# Verdicts of a frame with enough stars that still did not fit: on a clear night the image
# does not match the place (SPEC §10.7); "few stars" is a cloudy frame and says nothing.
FAILED_ON_CLEAR = ("no match", "large error", "close rival")


async def _failed_night(
    maker: async_sessionmaker[AsyncSession], device_id: str, taken: dt.datetime
) -> None:
    """Note the night the orientation failed on a clear frame (image_url.py reads it)."""
    async with maker() as db:
        device = await db.get(Device, device_id)
        place = _place(device) if device else None
        if device is None:
            return
        night = stations.night_id(taken, place[1] if place else None)
        status = dict(device.last_status or {})
        state = dict(status.get("solve") or {})
        before: list[Any] = state.get("fails") or []
        fails = [str(n) for n in before if n != night][-5:]
        state["fails"] = [*fails, night]
        status["solve"] = state
        device.last_status = status
        await db.commit()


def start(
    maker: async_sessionmaker[AsyncSession], device_id: str, data: bytes, taken: dt.datetime
) -> None:
    """Solve in the background, never in the request."""
    task = asyncio.get_running_loop().create_task(solve(maker, device_id, data, taken))
    task.add_done_callback(TASKS.discard)
    TASKS.add(task)
