"""indi-allsky's SyncAPI, understood as is (SharedSky, SPEC §10.3).

indi-allsky uploads to a remote indi-allsky server with ``<base>/sync/v1/<asset>``: a
multipart body with ``metadata`` (JSON) and ``media`` (the file), signed with
``Authorization: Bearer <user>:<hmac>``, HMAC-SHA3-512 of the secret over the time window
``floor(unix time / 300)`` (as decimal text) and the metadata's bytes. The owner enters the
hub's base URL, the station id as user and the station secret as API key. Its answers are
read as JSON with an ``id``. Only ``camera`` and ``image`` are used, the rest is dropped.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import math
import time
import zlib
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import EventVariant
from allskyhub_server.api import connect
from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import station_detect, stations
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.stations import SecretKey, StationError
from allskyhub_server.models import Device, EventRecord
from allskyhub_server.web.deps import DbSession

router = APIRouter(prefix="/indi-allsky/sync/v1")

TIME_SKEW = 300  # indi-allsky: requests_syncapi_v1.time_skew, syncapi_views.time_skew
WINDOWS = (0, -1, 1, -2, -3, -4)  # its own server's order; long uploads take time


def signature(secret: str, window: int, metadata: bytes) -> str:
    return hmac.new(
        secret.encode(), msg=str(window).encode() + metadata, digestmod=hashlib.sha3_512
    ).hexdigest()


async def station_from(
    request: Request, db: AsyncSession, metadata: bytes, now: float | None = None
) -> Device:
    """The station whose secret signed ``metadata``; a wrong one counts against the address
    (SPEC §10.2), as on ``connect/v1``."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    station_id, _, received = value.strip().partition(":")
    device = await db.get(Device, station_id) if len(station_id) == 26 else None
    good = False
    if (
        scheme == "Bearer"
        and received
        and device is not None
        and device.kind == "station"
        and device.owner_id is not None
        and device.station_secret_enc is not None
    ):
        secret = SecretKey(request.app.state.stores.vault).open(device.station_secret_enc)
        window = math.floor((now if now is not None else time.time()) / TIME_SKEW)
        good = any(
            hmac.compare_digest(signature(secret, window + d, metadata), received) for d in WINDOWS
        )
    if not good or device is None:
        raise await connect.wrong_login(request)
    try:
        await ratelimit.hit(request.app.state.engine, f"connect:{device.id}", connect.PER_STATION)
    except ratelimit.RateLimitedError as exc:
        raise StationError(429, "too_often", retry_after=exc.retry_after) from None
    return device


METADATA_MAX = 64 * 1024


async def read_signed(request: Request, db: AsyncSession) -> tuple[Device, bytes, bytes]:
    """The station, ``metadata`` and ``media`` of a SyncAPI request, read as a stream:
    indi-allsky sends ``metadata`` first, so its signature is checked as soon as that small
    part is complete, before the picture is read (security review of #390)."""
    await connect.ip_allowed(request)
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > connect.BODY_MAX:
        raise StationError(413, "too_large")
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise StationError(400, "bad_request", "metadata")
    parts: dict[str, bytearray] = {}
    done: set[str] = set()
    state: dict[str, Any] = {"name": None, "field": b"", "value": b"", "headers": {}}

    def on_part_begin() -> None:
        state.update(name=None, headers={})

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["field"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["value"] += data[start:end]

    def on_header_end() -> None:
        state["headers"][bytes(state["field"]).lower()] = bytes(state["value"])
        state.update(field=b"", value=b"")

    def on_headers_finished() -> None:
        _, disposition = parse_options_header(state["headers"].get(b"content-disposition", b""))
        name = disposition.get(b"name", b"").decode(errors="replace")
        state["name"] = name if name in ("metadata", "media") else None
        if state["name"]:
            parts[state["name"]] = bytearray()

    def on_part_data(data: bytes, start: int, end: int) -> None:
        name = state["name"]
        if name:
            parts[name] += data[start:end]
            limit = METADATA_MAX if name == "metadata" else connect.FRAME_MAX
            if len(parts[name]) > limit:
                raise StationError(413, "too_large")

    def on_part_end() -> None:
        if state["name"]:
            done.add(state["name"])

    callbacks: Any = {
        "on_part_begin": on_part_begin, "on_header_field": on_header_field,
        "on_header_value": on_header_value, "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished, "on_part_data": on_part_data,
        "on_part_end": on_part_end,
    }  # fmt: skip
    parser = MultipartParser(boundary, callbacks, max_size=connect.BODY_MAX)
    device: Device | None = None
    try:
        async for chunk in request.stream():
            parser.write(chunk)
            if device is None and "metadata" in done:
                device = await station_from(request, db, bytes(parts["metadata"]))
        parser.finalize()
    except MultipartParseError:
        raise StationError(400, "bad_request", "metadata") from None
    if not parts.get("metadata"):
        raise StationError(400, "bad_request", "metadata")
    if device is None:
        device = await station_from(request, db, bytes(parts["metadata"]))
    return device, bytes(parts["metadata"]), bytes(parts.get("media", b""))


def _id(device: Device) -> int:
    """A stable number for indi-allsky's ``sync_id``."""
    return zlib.crc32(device.id.encode()) & 0x7FFFFFFF


def _num(meta: dict[str, Any], key: str) -> float | None:
    value = meta.get(key)
    return float(value) if isinstance(value, int | float) and math.isfinite(value) else None


def _camera(device: Device, meta: dict[str, Any]) -> None:
    """Station data from indi-allsky's camera (SPEC §10.3); its frame size comes with the
    images, so it stays as known."""
    info = dict((device.last_status or {}).get("station") or {})
    lat, lon = _num(meta, "latitude"), _num(meta, "longitude")
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise StationError(400, "bad_request", "latitude")
    body = connect.StationIn(
        name=device.name or "Station",
        latitude=lat,
        longitude=lon,
        elevation_m=_num(meta, "elevation"),
        software="indi-allsky",
        software_version=None,
        lens=connect.Lens(
            focal_mm=_num(meta, "lensFocalLength"), f_ratio=_num(meta, "lensFocalRatio")
        ),
        width=int(info.get("width") or _num(meta, "width") or 1),
        height=int(info.get("height") or _num(meta, "height") or 1),
        clock_synced=True,
    )
    # indi-allsky's camera name is its driver ("ZWO ASI178MC"): the owner's name stays.
    name = device.name
    connect.apply_station(device, body)
    device.name = name


async def _image(
    db: AsyncSession,
    store: ImageStore,
    maker: async_sessionmaker[AsyncSession],
    device: Device,
    meta: dict[str, Any],
    data: bytes,
    detector: station_detect.Detector | None = None,
) -> None:
    """A frame (SPEC §10.3): the thumbnail as for ``POST /frames``; with ``detections`` the
    full frame is kept as a meteor candidate. Every frame also goes to the hub's own
    detection (devices/station_detect.py): when it finds meteors in the frame before, one
    indi-allsky saw streaks on, they replace that frame's candidate, with their tracks."""
    start_s, exposure = _num(meta, "createDate"), _num(meta, "exposure") or 0.0
    if start_s is None:
        raise StationError(400, "bad_request", "createDate")
    start = dt.datetime.fromtimestamp(start_s, dt.UTC)
    pic = await asyncio.to_thread(stations.picture, data)
    status = dict(device.last_status or {})
    info = dict(status.get("station") or {})
    if (info.get("width"), info.get("height")) != (pic.width, pic.height):
        info.update(width=pic.width, height=pic.height)
        status.update(station=info, orientation=None)
        device.last_status = status
    frame = connect.FrameIn(
        time=start + dt.timedelta(seconds=exposure / 2),
        exposure_s=exposure,
        gain=_num(meta, "gain"),
        night=bool(meta.get("night")),
        clock_synced=True,
        stars=int(_num(meta, "stars") or 0),
        sky_brightness=None,  # indi-allsky's sqm is its own scale, not mag/arcsec²
    )
    await connect.store_frame(db, store, maker, device, frame, data, full_always=True)
    detections = int(_num(meta, "detections") or 0)
    if detector is not None:
        found = await detector.feed(
            device.id, data, frame.time, exposure, "indi-allsky", detections=detections,
            orientation=(device.last_status or {}).get("orientation"),
        )  # fmt: skip
        if found is not None and found.held.detections > 0:
            await _with_tracks(db, store, device, found)
    if detections <= 0:
        return
    night = stations.night_id(start, stations.longitude(device))
    if await stations.night_full(db, device, night):
        raise StationError(429, "night_full", retry_after=3600)
    event_id = await stations.event_id(db, device, "meteor", start)
    await connect.save_pictures(store, device.id, night, event_id, data, pic.thumb)
    db.add(EventRecord(
        device_id=device.id, event_id=event_id, night_id=night, kind="meteor",
        start=start, end=start + dt.timedelta(seconds=exposure), confidence=0.5,
        has_image=True, has_thumb=True, has_original=True,
        original_w=pic.width, original_h=pic.height,
        data={
            "source": "indi-allsky", "candidate": True,
            "detections": int(_num(meta, "detections") or 0), "track_px": None, "peak": None,
            "start_azimuth_deg": None, "start_altitude_deg": None,
            "end_azimuth_deg": None, "end_altitude_deg": None, "track_ordered": False,
            "time_trusted": stations.clock_trusted(device, True),
        },
    ))  # fmt: skip
    await asyncio.to_thread(stations.cap_storage, store.root, device.id)


async def _with_tracks(
    db: AsyncSession, store: ImageStore, device: Device, found: station_detect.Found
) -> None:
    """The hub found meteors on a frame indi-allsky had a candidate for: they take its
    place (SPEC §10.3), with tracks, as trial events (devices/station_detect.py)."""
    held = found.held
    start = dt.datetime.fromtimestamp(held.taken - held.exposure_s / 2, dt.UTC)
    old = await db.scalar(
        select(EventRecord).where(
            EventRecord.device_id == device.id,
            EventRecord.kind == "meteor",
            EventRecord.start == start,
        )
    )
    if old is not None and (old.data or {}).get("candidate"):
        for variant in EventVariant:
            await asyncio.to_thread(
                store.delete_event, device.id, old.night_id, old.event_id, variant
            )
        await db.delete(old)
        await db.flush()
    await station_detect.store_found(db, store, device, found, "indi-allsky")


def _error(exc: StationError) -> JSONResponse:
    return connect.error(exc)


@router.api_route("/{asset}", methods=["GET", "POST", "PUT", "DELETE"])
async def sync(request: Request, db: DbSession, asset: str) -> JSONResponse:
    try:
        device, raw, data = await read_signed(request, db)
        try:
            meta: Any = json.loads(raw)
        except ValueError:
            raise StationError(400, "bad_request", "metadata") from None
        if not isinstance(meta, dict):
            raise StationError(400, "bad_request", "metadata")
        if asset == "camera":
            if request.method in ("POST", "PUT"):
                _camera(device, meta)  # pyright: ignore[reportUnknownArgumentType]
        elif asset == "image" and request.method in ("POST", "PUT"):
            facts: dict[str, Any] = meta  # pyright: ignore[reportUnknownVariableType]
            maker = request.app.state.sessionmaker
            await _image(
                db, request.app.state.images, maker, device, facts, data,
                request.app.state.station_detector,
            )  # fmt: skip
        elif asset == "image" and request.method == "GET":
            return JSONResponse({"error": "file_missing"}, status_code=400)
    except StationError as exc:
        return _error(exc)
    device.last_seen_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return JSONResponse({"id": _id(device), "url": ""})
