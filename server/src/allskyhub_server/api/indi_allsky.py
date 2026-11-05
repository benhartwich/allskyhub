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
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from allskyhub_server.api import connect
from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import stations
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
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    station_id, _, received = value.strip().partition(":")
    if scheme != "Bearer" or not station_id or not received:
        raise StationError(401, "unauthorized")
    device = await db.get(Device, station_id) if len(station_id) == 26 else None
    if (
        device is None
        or device.kind != "station"
        or device.owner_id is None
        or device.station_secret_enc is None
    ):
        raise StationError(401, "unauthorized")
    secret = SecretKey(request.app.state.stores.vault).open(device.station_secret_enc)
    window = math.floor((now if now is not None else time.time()) / TIME_SKEW)
    if not any(
        hmac.compare_digest(signature(secret, window + d, metadata), received) for d in WINDOWS
    ):
        raise StationError(401, "unauthorized")
    try:
        await ratelimit.hit(request.app.state.engine, f"connect:{device.id}", connect.PER_STATION)
    except ratelimit.RateLimitedError as exc:
        raise StationError(429, "too_often", retry_after=exc.retry_after) from None
    return device


async def _parts(request: Request) -> tuple[bytes, bytes]:
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > connect.BODY_MAX:
        raise StationError(413, "too_large")
    form = await request.form(max_files=2, max_fields=2)
    metadata, media = form.get("metadata"), form.get("media")
    raw = await metadata.read() if isinstance(metadata, UploadFile) else b""
    data = await media.read() if isinstance(media, UploadFile) else b""
    if not raw:
        raise StationError(400, "bad_request", "metadata")
    if len(data) > connect.FRAME_MAX:
        raise StationError(413, "too_large")
    return raw, data


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
    db: AsyncSession, store: ImageStore, device: Device, meta: dict[str, Any], data: bytes
) -> None:
    """A frame (SPEC §10.3): the thumbnail as for ``POST /frames``; with ``detections`` the
    full frame is kept as a meteor candidate for the hub's own detection."""
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
    await connect.store_frame(db, store, device, frame, pic.thumb)
    if (_num(meta, "detections") or 0) <= 0:
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


def _error(exc: StationError) -> JSONResponse:
    return connect.error(exc)


@router.api_route("/{asset}", methods=["GET", "POST", "PUT", "DELETE"])
async def sync(request: Request, db: DbSession, asset: str) -> JSONResponse:
    try:
        raw, data = await _parts(request)
        device = await station_from(request, db, raw)
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
            await _image(db, request.app.state.images, device, meta, data)  # pyright: ignore[reportUnknownArgumentType]
        elif asset == "image" and request.method == "GET":
            return JSONResponse({"error": "file_missing"}, status_code=400)
    except StationError as exc:
        return _error(exc)
    device.last_seen_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return JSONResponse({"id": _id(device), "url": ""})
