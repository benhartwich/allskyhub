"""The open API for connected cameras, ``connect/v1`` (SharedSky, SPEC §10.2).

Stations (devices/stations.py) send their data, frames and meteors over HTTPS with
``Authorization: Bearer <station id>:<secret>``. ``/test/…`` checks the same requests and
answers what it understood, storing nothing (§10.5).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import UploadFile

from allskyhub_protocol import EventVariant, FrameVariant
from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import meteor_pairs, past_orientations, station_solve, stations
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.stations import StationError
from allskyhub_server.models import Device, EventRecord, Frame, SkySample
from allskyhub_server.web.deps import DbSession, client_ip
from allskyhub_sky.astro import sun_altitude

router = APIRouter(prefix="/api/connect/v1")
M = TypeVar("M", bound=BaseModel)

PER_STATION = (ratelimit.Limit(120, 60),)
FRAME_MAX = 8 * 1024 * 1024
THUMB_MAX = 300 * 1024
BODY_MAX = FRAME_MAX + 64 * 1024  # the frame and its metadata


class Lens(BaseModel):
    model_config = ConfigDict(extra="ignore")

    focal_mm: float | None = Field(default=None, gt=0, le=1000)
    f_ratio: float | None = Field(default=None, gt=0, le=100)
    projection: str | None = Field(default=None, max_length=40)


class StationIn(BaseModel):
    """SPEC §10.2 ``PUT /station``."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    elevation_m: float | None = Field(default=None, ge=-500, le=9000)
    software: str | None = Field(default=None, max_length=40)
    software_version: str | None = Field(default=None, max_length=40)
    lens: Lens | None = None
    width: int = Field(gt=0, le=20000)
    height: int = Field(gt=0, le=20000)
    clock_synced: bool = False


class FrameIn(BaseModel):
    """SPEC §10.2 ``POST /frames`` metadata; ``time`` is mid-exposure."""

    model_config = ConfigDict(extra="ignore")

    time: AwareDatetime
    exposure_s: float = Field(ge=0, le=3600)
    gain: float | None = Field(default=None, ge=0, le=100000)
    night: bool
    clock_synced: bool = False
    stars: int | None = Field(default=None, ge=0, le=1_000_000)
    sky_brightness: float | None = Field(default=None, ge=0, le=30)


class EventIn(BaseModel):
    """SPEC §10.2 ``POST /events`` metadata; ``track`` in pixels of the full frame."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["meteor"]
    start: AwareDatetime
    end: AwareDatetime
    peak: float | None = Field(default=None, ge=0, le=1)
    track: list[tuple[float, float]] = Field(min_length=2, max_length=32)
    clock_synced: bool = False


def error(exc: StationError) -> JSONResponse:
    body: dict[str, Any] = {"error": exc.code}
    if exc.field:
        body["field"] = exc.field
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(body, status_code=exc.status, headers=headers)


def _ip_key(request: Request) -> str:
    return f"station-auth:{client_ip(request, request.app.state.settings)}"


async def ip_allowed(request: Request) -> None:
    """Before anything is read: an address with too many wrong logins waits (SPEC §10.2,
    like password guesses); checking does not count."""
    retry = await ratelimit.blocked(
        request.app.state.engine, _ip_key(request), ratelimit.STATION_AUTH_PER_IP
    )
    if retry:
        raise StationError(429, "too_often", retry_after=retry)


async def wrong_login(request: Request) -> StationError:
    """Count a wrong login of this address; the answer to give."""
    try:
        await ratelimit.hit(
            request.app.state.engine, _ip_key(request), ratelimit.STATION_AUTH_PER_IP
        )
    except ratelimit.RateLimitedError as exc:
        return StationError(429, "too_often", retry_after=exc.retry_after)
    return StationError(401, "unauthorized")


async def station_from(request: Request, db: AsyncSession) -> Device:
    """The station of ``Authorization: Bearer <id>:<secret>``, counted against its limit;
    checked from the header alone, before the body is read."""
    await ip_allowed(request)
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    station_id, _, secret = value.strip().partition(":")
    try:
        if scheme.lower() != "bearer" or not station_id or not secret:
            raise StationError(401, "unauthorized")
        device = await stations.authenticate(db, station_id, secret)
    except StationError:
        raise await wrong_login(request) from None
    try:
        await ratelimit.hit(request.app.state.engine, f"connect:{device.id}", PER_STATION)
    except ratelimit.RateLimitedError as exc:
        raise StationError(429, "too_often", retry_after=exc.retry_after) from None
    return device


def parse(model: type[M], raw: bytes | str) -> M:
    try:
        return model.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        field = None
        if isinstance(exc, ValidationError) and exc.errors():
            field = ".".join(str(p) for p in exc.errors()[0]["loc"]) or None
        raise StationError(400, "bad_request", field or "metadata") from None


async def multipart(request: Request) -> tuple[bytes, bytes]:
    """The ``metadata`` and ``image`` parts (SPEC §10.2)."""
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > BODY_MAX:
        raise StationError(413, "too_large")
    form = await request.form(max_files=2, max_fields=2)
    metadata, image = form.get("metadata"), form.get("image")
    raw = await metadata.read() if isinstance(metadata, UploadFile) else metadata
    if not raw:
        raise StationError(400, "bad_request", "metadata")
    if not isinstance(image, UploadFile):
        raise StationError(400, "bad_request", "image")
    data = await image.read()
    if len(data) > FRAME_MAX:
        raise StationError(413, "too_large")
    return raw.encode() if isinstance(raw, str) else raw, data


def _station_info(device: Device) -> dict[str, Any]:
    return dict((device.last_status or {}).get("station") or {})


def _answer(device: Device) -> dict[str, Any]:
    return {
        "orientation": (device.last_status or {}).get("orientation"),
        "want_full_frame": station_solve.want_full_frame(device, dt.datetime.now(dt.UTC)),
    }


def apply_station(device: Device, body: StationIn) -> None:
    """Station data into the device (SPEC §10.2): the place where pairing reads it; a new
    frame size drops the orientation."""
    status: dict[str, Any] = dict(device.last_status or {})
    old = _station_info(device)
    status["settings"] = {
        "latitude": body.latitude,
        "longitude": body.longitude,
        "elevation_m": body.elevation_m,
    }
    status["station"] = {
        "software": body.software,
        "software_version": body.software_version,
        "lens": body.lens.model_dump() if body.lens else None,
        "width": body.width,
        "height": body.height,
        "clock_synced": body.clock_synced,
    }
    if (old.get("width"), old.get("height")) != (body.width, body.height):
        past_orientations.dropped(device, dt.datetime.now(dt.UTC))
        status["orientation"] = None
    device.last_status = status
    device.name = body.name.strip()[:100]
    device.agent_version = (body.software_version or "")[:64]
    device.last_seen_at = dt.datetime.now(dt.UTC)


@router.put("/station")
async def put_station(request: Request, db: DbSession) -> JSONResponse:
    try:
        device = await station_from(request, db)
        body = parse(StationIn, await request.body())
    except StationError as exc:
        return error(exc)
    apply_station(device, body)
    await db.commit()
    return JSONResponse(_answer(device))


async def store_frame(
    db: AsyncSession,
    store: ImageStore,
    maker: async_sessionmaker[AsyncSession],
    device: Device,
    meta: FrameIn,
    data: bytes,
    *,
    full_always: bool = False,
) -> bool:
    """SPEC §10.2: a thumbnail at most once a minute; the clock is judged on every frame.
    A full frame (the station's ``width`` by ``height``) while the hub wants one is kept and
    solved in the background (§10.1); otherwise a full frame is refused, unless the sender
    always sends full ones (``full_always``, indi-allsky). Returns ``want_full_frame``."""
    now = dt.datetime.now(dt.UTC)
    stations.check_clock(device, meta.time, meta.exposure_s, now)
    device.last_seen_at = now
    device.last_frame = meta.model_dump(mode="json")
    if meta.night and (meta.sky_brightness is not None or meta.stars is not None):
        # SPEC §10.2: the station's own sky measurement, kept like a camera's (§6.3).
        await db.execute(
            pg_insert(SkySample)
            .values(
                device_id=device.id,
                night_id=stations.night_id(meta.time, stations.longitude(device)),
                at=meta.time,
                sqm_mag=meta.sky_brightness,
                stars=meta.stars,
            )
            .on_conflict_do_nothing(index_elements=["device_id", "at"])
        )
    pic = await asyncio.to_thread(stations.picture, data)
    info = _station_info(device)
    full = (pic.width, pic.height) == (info.get("width"), info.get("height"))
    wanted = station_solve.want_full_frame(device, now)
    if full and wanted:
        station_solve.count_try(device, now)
        await _keep(db, store, device, meta, pic, now, data)
        await db.commit()  # the solve writes the orientation after this request's data
        station_solve.start(maker, device.id, data, meta.time)
        return station_solve.want_full_frame(device, now)
    if len(data) > THUMB_MAX and not full_always:
        raise StationError(413, "too_large")  # a full frame the hub did not ask for
    if device.latest_thumb_at is not None and now - device.latest_thumb_at < stations.FRAME_EVERY:
        return wanted
    await _keep(db, store, device, meta, pic, now, None)
    return wanted


async def _keep(
    db: AsyncSession,
    store: ImageStore,
    device: Device,
    meta: FrameIn,
    pic: stations.Picture,
    now: dt.datetime,
    full: bytes | None,
) -> None:
    """The frame's thumbnail and, if given, the full frame, with its ``Frame`` row."""
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    night = stations.night_id(meta.time, stations.longitude(device))
    name = f"{meta.time:%Y%m%dT%H%M%S}Z.jpg"
    sun = (
        sun_altitude(meta.time, float(lat), float(lon))
        if isinstance(lat, int | float) and isinstance(lon, int | float)
        else 0.0
    )
    await asyncio.to_thread(store.save_frame, device.id, night, name, FrameVariant.THUMB, pic.thumb)
    if full is not None:
        await asyncio.to_thread(store.save_frame, device.id, night, name, FrameVariant.FULL, full)
        device.latest_image_at = now
    values = {
        "device_id": device.id, "night_id": night, "name": name, "captured_at": meta.time,
        "mode": "night" if meta.night else "day", "exposure_us": round(meta.exposure_s * 1e6),
        "gain": meta.gain or 0.0, "mean": pic.mean, "sun_elevation": sun, "has_thumb": True,
        "has_full": full is not None,
    }  # fmt: skip
    await db.execute(
        pg_insert(Frame)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["device_id", "night_id", "name"])
    )
    device.latest_thumb_at = now
    if full is not None:
        await asyncio.to_thread(stations.cap_storage, store.root, device.id)


@router.post("/frames")
async def post_frame(request: Request, db: DbSession) -> JSONResponse:
    try:
        device = await station_from(request, db)
        raw, data = await multipart(request)
        meta = parse(FrameIn, raw)
        want = await store_frame(
            db, request.app.state.images, request.app.state.sessionmaker, device, meta, data
        )
    except StationError as exc:
        return error(exc)
    await db.commit()
    return JSONResponse({"want_full_frame": want})


async def save_pictures(
    store: ImageStore, device_id: str, night: str, event_id: str, full: bytes, thumb: bytes
) -> None:
    """An event's whole frame (``original``, SPEC §6.4) and its thumbnail."""
    for variant, data in ((EventVariant.ORIGINAL, full), (EventVariant.THUMB, thumb)):
        await asyncio.to_thread(store.save_event, device_id, night, event_id, variant, data)


async def store_event(
    db: AsyncSession, store: ImageStore, device: Device, meta: EventIn, data: bytes
) -> str:
    """SPEC §10.2: the meteor as an event of §6.4. Its directions come from the track
    with the station's orientation, or stay null until the hub has one (§10.1)."""
    info = _station_info(device)
    pic = await asyncio.to_thread(stations.picture, data)
    if (pic.width, pic.height) != (info.get("width"), info.get("height")):
        raise StationError(400, "wrong_size")
    night = stations.night_id(meta.start, stations.longitude(device))
    if await stations.night_full(db, device, night):
        raise StationError(429, "night_full", retry_after=3600)
    track = [(float(x), float(y)) for x, y in meta.track]
    if not all(0 <= x <= pic.width and 0 <= y <= pic.height for x, y in track):
        raise StationError(400, "bad_request", "track")
    peak = meta.peak
    if peak is None:
        peak = await asyncio.to_thread(stations.track_peak, data, track)
    event_id = await stations.event_id(db, device, "meteor", meta.start)
    await save_pictures(store, device.id, night, event_id, data, pic.thumb)
    found = (device.last_status or {}).get("orientation")
    sol = station_solve.from_orientation(found) if found else None
    sky = station_solve.directions(sol, [list(p) for p in track]) if sol else None
    db.add(EventRecord(
        device_id=device.id, event_id=event_id, night_id=night, kind="meteor",
        start=meta.start, end=meta.end, confidence=1.0, has_image=True, has_thumb=True,
        has_original=True, original_w=pic.width, original_h=pic.height,
        data={
            "source": "connect", "track_px": [list(p) for p in track], "peak": peak,
            "start_azimuth_deg": None, "start_altitude_deg": None,
            "end_azimuth_deg": None, "end_altitude_deg": None, "track_ordered": False,
            "time_trusted": stations.clock_trusted(device, meta.clock_synced),
        } | (sky or {}),
    ))  # fmt: skip
    await asyncio.to_thread(stations.cap_storage, store.root, device.id)
    return event_id


@router.post("/events")
async def post_event(request: Request, db: DbSession) -> JSONResponse:
    try:
        device = await station_from(request, db)
        raw, data = await multipart(request)
        meta = parse(EventIn, raw)
        event_id = await store_event(db, request.app.state.images, device, meta, data)
    except StationError as exc:
        return error(exc)
    device.last_seen_at = dt.datetime.now(dt.UTC)
    await db.commit()
    stored = await db.scalar(
        select(EventRecord.data).where(
            EventRecord.device_id == device.id, EventRecord.event_id == event_id
        )
    )
    if stored and stored.get("time_trusted") and stored.get("start_azimuth_deg") is not None:
        await meteor_pairs.pair_event(request.app.state.sessionmaker, device.id, event_id)
    return JSONResponse({"id": event_id})


# SPEC §10.5: the same checks, nothing stored.


@router.put("/test/station")
async def test_station(request: Request, db: DbSession) -> JSONResponse:
    try:
        await station_from(request, db)
        body = parse(StationIn, await request.body())
    except StationError as exc:
        return error(exc)
    return JSONResponse({"ok": True, "understood": body.model_dump(mode="json")})


@router.post("/test/frames")
async def test_frame(request: Request, db: DbSession) -> JSONResponse:
    try:
        await station_from(request, db)
        raw, data = await multipart(request)
        meta = parse(FrameIn, raw)
        pic = await asyncio.to_thread(stations.picture, data)
    except StationError as exc:
        return error(exc)
    understood = meta.model_dump(mode="json") | {"image": [pic.width, pic.height]}
    return JSONResponse({"ok": True, "understood": understood})


@router.post("/test/events")
async def test_event(request: Request, db: DbSession) -> JSONResponse:
    try:
        device = await station_from(request, db)
        raw, data = await multipart(request)
        meta = parse(EventIn, raw)
        pic = await asyncio.to_thread(stations.picture, data)
        info = _station_info(device)
        if (pic.width, pic.height) != (info.get("width"), info.get("height")):
            raise StationError(400, "wrong_size")
    except StationError as exc:
        return error(exc)
    understood = meta.model_dump(mode="json") | {"image": [pic.width, pic.height]}
    return JSONResponse({"ok": True, "understood": understood})
