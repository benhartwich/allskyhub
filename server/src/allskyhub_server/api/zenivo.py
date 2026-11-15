"""Zenivo's sync, understood as is (SharedSky, SPEC §10.9).

Zenivo (github.com/hoanb1/zenivo) pushes to ``--sync-target`` once a minute: a frame as
``POST <base>/api/allsky/ingest/frame`` (multipart ``image``, headers ``X-Exposure-Sec``
and ``X-Gain-Db``), right after it its telemetry as ``POST <base>/api/allsky/ingest/
telemetry`` (JSON with ``capture_time``). Its running client sends no token, so the owner's
base is ``https://<hub>/zenivo/<station id>.<secret>``: the secret is in the path, which the
hub's logs leave out (app.py ``log_path``, the nginx configuration).

A frame waits for its telemetry, which gives its time; one without (none came within
``TELEMETRY_WAIT``) takes its arrival and makes the station untrusted for pairing. The
frames carry Zenivo's text stamp and come with gaps between exposures: they are processed
like images of §10.7 (overlay mask, the hub's meteor detection on runs, trial events).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from allskyhub_server.api import connect
from allskyhub_server.devices import image_url, station_detect, station_flow, stations
from allskyhub_server.devices.stations import StationError
from allskyhub_server.models import Device
from allskyhub_server.web.deps import DbSession
from allskyhub_sky.astro import sun_altitude

router = APIRouter(prefix="/zenivo/{token}/api/allsky/ingest")

SOFTWARE = "zenivo"
SOURCE = "zenivo"  # events' data["source"]
TELEMETRY_WAIT = dt.timedelta(seconds=30)
TELEMETRY_MAX = 256 * 1024  # with its 256-bin histogram a few kB
INTERVAL_S = 60.0  # Zenivo's default --sync-interval


@dataclass(frozen=True)
class Waiting:
    """A frame waiting for its telemetry."""

    jpeg: bytes
    exposure_s: float
    gain: float | None
    arrived: dt.datetime


class Pending:
    """The last frame of each station until its telemetry comes (one process, like the
    station detector's buffer)."""

    def __init__(self) -> None:
        self._frames: dict[str, Waiting] = {}

    def put(self, device_id: str, frame: Waiting) -> Waiting | None:
        """Keep ``frame``; the one it replaces, which got no telemetry."""
        old = self._frames.pop(device_id, None)
        self._frames[device_id] = frame
        return old

    def take(self, device_id: str) -> Waiting | None:
        return self._frames.pop(device_id, None)

    def drop(self, device_id: str) -> None:
        self._frames.pop(device_id, None)


def split_token(token: str) -> tuple[str, str]:
    """``<station id>.<secret>`` of the path."""
    station_id, _, secret = token.partition(".")
    return station_id, secret


async def _station(request: Request, db: AsyncSession, token: str) -> Device:
    station_id, secret = split_token(token)
    return await connect.station_by_secret(request, db, station_id, secret, SOFTWARE)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    return float(value) if isinstance(value, int | float) and math.isfinite(value) else None


def capture_time(telemetry: dict[str, Any]) -> dt.datetime | None:
    """Zenivo's ``capture_time``: the end of the capture on the camera's clock, ISO 8601
    with its offset."""
    raw = telemetry.get("capture_time")
    if not isinstance(raw, str):
        return None
    try:
        at = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return at.astimezone(dt.UTC) if at.tzinfo is not None else None


async def _process(
    request: Request,
    db: AsyncSession,
    device: Device,
    frame: Waiting,
    telemetry: dict[str, Any] | None,
) -> None:
    """A frame of §10.2 with its time (§10.9), then the overlay mask and the meteor
    detection of §10.7."""
    state = request.app.state
    end = capture_time(telemetry) if telemetry is not None else None
    half = dt.timedelta(seconds=frame.exposure_s / 2)
    taken = (end or frame.arrived) - half
    origin = "station" if end is not None else "fetch"
    pic = await asyncio.to_thread(stations.picture, frame.jpeg)
    image_url.apply_size(device, pic.width, pic.height)
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    sun = (
        sun_altitude(taken, float(lat), float(lon))
        if isinstance(lat, int | float) and isinstance(lon, int | float)
        else 0.0
    )
    stars = _number((telemetry or {}).get("star_count"))
    meta = connect.FrameIn(
        time=taken,
        exposure_s=min(max(frame.exposure_s, 0.0), 3600.0),
        gain=frame.gain if frame.gain is not None and 0 <= frame.gain <= 100000 else None,
        night=sun <= -6.0,
        clock_synced=end is not None,
        stars=int(stars) if stars is not None and 0 <= stars <= 1_000_000 else None,
    )
    await connect.store_frame(
        db, state.images, state.sessionmaker, device, meta, frame.jpeg, full_always=True
    )
    device.last_frame = dict(device.last_frame or {}) | {"time_source": origin}
    if end is None:
        clock = dict(device.clock or {})
        clock["trusted"] = False  # SPEC §10.9: like §10.7's fetch time, never for pairing
        device.clock = clock
    masks = image_url.OverlayMask(station_flow.masks_dir(state.settings))
    await asyncio.to_thread(masks.update, device.id, frame.jpeg)
    overlay = await asyncio.to_thread(masks.mask, device.id)
    detector: station_detect.Detector = state.station_detector
    found = await detector.feed(
        device.id, frame.jpeg, taken, frame.exposure_s, origin, usual_s=INTERVAL_S * 2,
        overlay=overlay, orientation=(device.last_status or {}).get("orientation"),
    )  # fmt: skip
    if found is not None:
        await station_detect.store_found(db, state.images, device, found, SOURCE)


def _ok() -> JSONResponse:
    return JSONResponse({"ok": True})


@router.post("/frame")
async def frame(request: Request, db: DbSession, token: str) -> JSONResponse:
    """A frame; it waits for the telemetry that follows it."""
    try:
        device = await _station(request, db, token)
        length = request.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > connect.BODY_MAX:
            raise StationError(413, "too_large")
        form = await request.form(max_files=1, max_fields=2)
        image = form.get("image")
        if not isinstance(image, UploadFile):
            raise StationError(400, "bad_request", "image")
        data = await image.read()
        if len(data) > connect.FRAME_MAX:
            raise StationError(413, "too_large")
        await asyncio.to_thread(stations.picture, data)  # bad_image now, not later
        exposure = _number(request.headers.get("x-exposure-sec")) or 0.0
        waiting = Waiting(
            jpeg=data,
            exposure_s=max(exposure, 0.0),
            gain=_number(request.headers.get("x-gain-db")),
            arrived=dt.datetime.now(dt.UTC),
        )
        pending: Pending = request.app.state.zenivo
        old = pending.put(device.id, waiting)
        if old is not None:
            await _process(request, db, device, old, None)
    except StationError as exc:
        return connect.error(exc)
    device.last_seen_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return _ok()


@router.post("/telemetry")
async def telemetry(request: Request, db: DbSession, token: str) -> JSONResponse:
    """The telemetry of the frame just before; it gives the frame its time."""
    try:
        device = await _station(request, db, token)
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > TELEMETRY_MAX:
                raise StationError(413, "too_large")
        try:
            body: Any = json.loads(raw)
        except ValueError:
            raise StationError(400, "bad_request", "telemetry") from None
        if not isinstance(body, dict):
            raise StationError(400, "bad_request", "telemetry")
        facts: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
        pending: Pending = request.app.state.zenivo
        waiting = pending.take(device.id)
        if waiting is not None:
            fresh = dt.datetime.now(dt.UTC) - waiting.arrived <= TELEMETRY_WAIT
            await _process(request, db, device, waiting, facts if fresh else None)
    except StationError as exc:
        return connect.error(exc)
    device.last_seen_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return _ok()


@router.post("/{kind}")
async def other(request: Request, db: DbSession, token: str, kind: str) -> JSONResponse:
    """``event`` and ``summary`` are answered and dropped (§10.9); other paths are 404."""
    if kind not in ("event", "summary"):
        return JSONResponse({"error": "not_found"}, status_code=404)
    try:
        await _station(request, db, token)
    except StationError as exc:
        return connect.error(exc)
    async for _ in request.stream():  # read and drop the body
        pass
    return _ok()
