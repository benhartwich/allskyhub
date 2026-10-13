"""Device API v1 (SPEC §6.1, §6.6): challenge, register, token, WebSocket, uploads."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import os
import pathlib
import tempfile
import time
from collections.abc import Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Request, WebSocket
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocketDisconnect, WebSocketState

from allskyhub_protocol import (
    EVENT_ID_PATTERN,
    PRODUCT_NAMES,
    Ack,
    Aircraft,
    ChallengeRequest,
    ChallengeResponse,
    CloseCode,
    ErrorReply,
    Event,
    EventLabel,
    FrameInfo,
    FrameVariant,
    Hello,
    NightDeleted,
    ProductKind,
    Products,
    PublicState,
    PublicUpdate,
    RegisterRequest,
    RegisterResponse,
    SkyMetrics,
    Status,
    TokenRequest,
    TokenResponse,
    parse_envelope,
)
from allskyhub_server.auth import ratelimit
from allskyhub_server.auth.accounts import DEFAULT_EVENT_KEEP_DAYS
from allskyhub_server.auth.hub_keys import HubKeys
from allskyhub_server.devices import (
    aircraft,
    labels,
    local_access,
    meteor_pairs,
    pairing,
    public,
    settings_form,
)
from allskyhub_server.devices.connections import Connection, ConnectionRegistry
from allskyhub_server.devices.discord import Discord
from allskyhub_server.devices.events import image_rev
from allskyhub_server.devices.images import JPEG_MAGIC, ImageStore
from allskyhub_server.devices.space_weather import KpCache
from allskyhub_server.models import (
    Device,
    EventRecord,
    Frame,
    Product,
    SkySample,
    StatusSample,
    User,
)
from allskyhub_server.push.notify import Notifier
from allskyhub_server.settings import Settings
from allskyhub_server.web.deps import DbSession, SettingsDep, client_ip

log = logging.getLogger(__name__)
router = APIRouter(prefix="/device/v1")

NIGHT_ID = r"^\d{8}$"
FRAME_NAME = r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$"  # never "." or ".."


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _limit(request: Request, key: str, limits: tuple[ratelimit.Limit, ...]) -> None:
    try:
        await ratelimit.hit(request.app.state.engine, key, limits)
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Too many requests", {"Retry-After": str(exc.retry_after)}
        ) from None


@router.post("/challenge")
async def challenge(
    request: Request, body: ChallengeRequest, db: DbSession, settings: SettingsDep
) -> ChallengeResponse:
    await _limit(
        request, f"dev-challenge:{client_ip(request, settings)}", ratelimit.DEVICE_CHALLENGE_PER_IP
    )
    nonce = await pairing.new_nonce(db, body.device_id)
    await db.commit()
    return ChallengeResponse(nonce=nonce, expires_in=int(pairing.NONCE_TTL.total_seconds()))


@router.post("/register")
async def register(
    request: Request, body: RegisterRequest, db: DbSession, settings: SettingsDep
) -> RegisterResponse:
    """SPEC §6.2 step 2."""
    await _limit(
        request, f"dev-register:{client_ip(request, settings)}", ratelimit.DEVICE_REGISTER_PER_IP
    )
    try:
        reg = await pairing.register(
            db,
            public_key_b64=body.public_key,
            profile=body.profile,
            agent_version=body.agent_version,
            nonce=body.nonce,
            signature=body.signature,
        )
    except pairing.InvalidProofError as exc:
        await db.commit()  # the nonce is spent either way
        raise HTTPException(401, str(exc)) from None
    await db.commit()
    return RegisterResponse(
        device_id=reg.device_id,
        paired=reg.paired,
        pairing_code=reg.code,
        expires_in=reg.expires_in,
    )


@router.post("/token")
async def token(
    request: Request, body: TokenRequest, db: DbSession, settings: SettingsDep
) -> TokenResponse:
    """SPEC §6.2 step 5."""
    await _limit(
        request, f"dev-token:{client_ip(request, settings)}", ratelimit.DEVICE_TOKEN_PER_IP
    )
    try:
        issued = await pairing.issue_token(db, body.device_id, body.nonce, body.signature)
    except pairing.InvalidProofError as exc:
        await db.commit()
        raise HTTPException(401, str(exc)) from None
    except pairing.NotPairedError as exc:
        await db.commit()
        raise HTTPException(403, str(exc)) from None
    await db.commit()
    keys: HubKeys = request.app.state.hub_keys
    return TokenResponse(
        access_token=issued.token, expires_in=issued.expires_in, hub_keys=keys.public()
    )


@router.get("/public")
async def public_get(request: Request, db: DbSession, settings: SettingsDep) -> PublicState:
    """SPEC §6.8: the camera's public page, for its local UI."""
    info = await _device_from_bearer(request, db)
    device = await db.get(Device, info.device_id)
    if device is None:
        raise HTTPException(401, "Invalid token")
    return public.state(device, settings.base_url)


@router.put("/public")
async def public_put(request: Request, db: DbSession, settings: SettingsDep) -> PublicState:
    """SPEC §6.8: switch the page and/or replace the profile; 422 with a German sentence."""
    info = await _device_from_bearer(request, db)
    device = await db.get(Device, info.device_id)
    if device is None:
        raise HTTPException(401, "Invalid token")
    try:
        body = PublicUpdate.model_validate_json(await request.body())
    except ValidationError as exc:
        raise HTTPException(422, public.describe_error(exc)) from None
    if body.profile is not None:
        device.public_profile = body.profile.model_dump()
    if body.enabled is True:
        public.enable(device)
    elif body.enabled is False:
        public.disable(device)
    await db.commit()
    return public.state(device, settings.base_url)


def _bearer(headers: Any) -> str | None:
    auth = str(headers.get("authorization", ""))
    scheme, _, value = auth.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def _device_from_bearer(request: Request, db: AsyncSession) -> pairing.TokenInfo:
    token_value = _bearer(request.headers)
    info = await pairing.device_for_token(db, token_value) if token_value else None
    if info is None:
        raise HTTPException(401, "Invalid token", {"WWW-Authenticate": "Bearer"})
    return info


async def _read_jpeg(request: Request, settings: Settings) -> bytes:
    """The body as a JPEG of at most ``max_image_mb``: 413 above it, 400 if not a JPEG."""
    limit = settings.max_image_mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "Image too large")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, "Image too large")
    if not data.startswith(JPEG_MAGIC):
        raise HTTPException(400, "Not a JPEG image")
    return bytes(data)


@router.put("/frames/{night_id}/{name}", status_code=204)
async def upload_frame(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    night_id: Annotated[str, Path(pattern=NIGHT_ID)],
    name: Annotated[str, Path(pattern=FRAME_NAME)],
    variant: Annotated[FrameVariant, Query()] = FrameVariant.FULL,
) -> Response:
    """SPEC §6.5: only uploads the hub asked for are accepted."""
    info = await _device_from_bearer(request, db)
    registry: ConnectionRegistry = request.app.state.connections
    # Checked before reading the body, taken only after it is valid, so a failed upload can
    # be retried within the window.
    if not registry.is_requested(info.device_id, night_id, name, variant):
        raise HTTPException(404, "Upload was not requested")
    data = await _read_jpeg(request, settings)
    if not registry.take_upload(info.device_id, night_id, name, variant):
        raise HTTPException(404, "Upload was not requested")  # taken by a parallel upload
    store: ImageStore = request.app.state.images
    await asyncio.to_thread(store.save_frame, info.device_id, night_id, name, variant, data)
    full = variant is FrameVariant.FULL
    await db.execute(
        update(Frame)
        .where(Frame.device_id == info.device_id, Frame.night_id == night_id, Frame.name == name)
        .values({"has_full": True} if full else {"has_thumb": True})
    )
    await db.execute(
        update(Device)
        .where(Device.id == info.device_id)
        .values({"latest_image_at": _now()} if full else {"latest_thumb_at": _now()})
    )
    await db.commit()
    return Response(status_code=204)


@router.put("/events/{night_id}/{event_id}", status_code=204)
async def upload_event(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    night_id: Annotated[str, Path(pattern=NIGHT_ID)],
    event_id: Annotated[str, Path(pattern=EVENT_ID_PATTERN)],
    variant: Annotated[FrameVariant, Query()] = FrameVariant.FULL,
) -> Response:
    """SPEC §6.5, §6.6: the picture of a requested event, like a frame."""
    info = await _device_from_bearer(request, db)
    registry: ConnectionRegistry = request.app.state.connections
    if not registry.is_requested(info.device_id, night_id, event_id, variant, "event"):
        raise HTTPException(404, "Upload was not requested")
    data = await _read_jpeg(request, settings)
    if not registry.take_upload(info.device_id, night_id, event_id, variant, "event"):
        raise HTTPException(404, "Upload was not requested")
    store: ImageStore = request.app.state.images
    await asyncio.to_thread(store.save_event, info.device_id, night_id, event_id, variant, data)
    await db.execute(
        update(EventRecord)
        .where(EventRecord.device_id == info.device_id, EventRecord.event_id == event_id)
        .values({"has_full": True} if variant is FrameVariant.FULL else {"has_thumb": True})
    )
    await db.commit()
    discord: Discord | None = request.app.state.notifier.discord
    if discord is not None and variant is FrameVariant.THUMB:
        # Roadmap H5: detections with a picture go to Discord once it is here.
        _background(discord.event(info.device_id, event_id, picture_arrived=True))
    return Response(status_code=204)


_PRODUCT_TYPES = dict(PRODUCT_NAMES.values())
MP4_BRAND_OFFSET = 4  # ISO BMFF: size (4 bytes), then the box type "ftyp"


def _looks_like(content_type: str, head: bytes) -> bool:
    if content_type == "image/jpeg":
        return head.startswith(JPEG_MAGIC)
    return head[MP4_BRAND_OFFSET : MP4_BRAND_OFFSET + 4] == b"ftyp"


@router.put("/products/{night_id}/{name}", status_code=204)
async def upload_product(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    night_id: Annotated[str, Path(pattern=NIGHT_ID)],
    name: str,
    variant: Annotated[FrameVariant, Query()] = FrameVariant.FULL,
) -> Response:
    """SPEC §6.5, §6.6: a requested night product, streamed to disk (up to
    ``max_product_mb``). Rejected uploads keep the request, so the device can retry."""
    info = await _device_from_bearer(request, db)
    if name not in _PRODUCT_TYPES:
        raise HTTPException(404, "Unknown product")
    registry: ConnectionRegistry = request.app.state.connections
    if not registry.is_requested(info.device_id, night_id, name, variant, "product"):
        raise HTTPException(404, "Upload was not requested")
    expected = _PRODUCT_TYPES[name] if variant is FrameVariant.FULL else "image/jpeg"
    if request.headers.get("content-type", "").split(";")[0].strip() != expected:
        raise HTTPException(400, f"Content-Type must be {expected}")
    limit = settings.max_product_mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "Product too large")

    store: ImageStore = request.app.state.images
    target = store.product_path(info.device_id, night_id, name, variant)
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".part")
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            size, head, buffer = 0, b"", bytearray()
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "Product too large")
                if len(head) < 12:
                    head = (head + chunk)[:12]
                buffer.extend(chunk)
                if len(buffer) >= 1024 * 1024:
                    await asyncio.to_thread(fh.write, bytes(buffer))
                    buffer.clear()
            await asyncio.to_thread(fh.write, bytes(buffer))
        if not _looks_like(expected, head):
            raise HTTPException(400, f"Body is not {expected}")
        if not registry.take_upload(info.device_id, night_id, name, variant, "product"):
            raise HTTPException(404, "Upload was not requested")
        await asyncio.to_thread(tmp.replace, target)
    finally:
        await asyncio.to_thread(tmp.unlink, missing_ok=True)
    full = variant is FrameVariant.FULL
    await db.execute(
        update(Product)
        .where(
            Product.device_id == info.device_id,
            Product.night_id == night_id,
            Product.name == name,
        )
        .values({"has_full": True, "full_at": _now()} if full else {"has_thumb": True})
    )
    await db.commit()
    return Response(status_code=204)


# --- WebSocket (SPEC §6.1, §6.3, §6.5) ------------------------------------------------------


@router.websocket("/ws")
async def device_ws(websocket: WebSocket) -> None:
    app = websocket.app
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    settings: Settings = app.state.settings
    registry: ConnectionRegistry = app.state.connections
    notifier: Notifier = app.state.notifier
    store: ImageStore = app.state.images

    token_value = _bearer(websocket.headers)
    async with maker() as db:
        info = await pairing.device_for_token(db, token_value) if token_value else None
    # Accept first: close codes only reach the device on an open WebSocket.
    await websocket.accept()
    if info is None:
        await websocket.close(CloseCode.REAUTH)
        return

    async def send_text(text: str) -> None:
        await websocket.send_text(text)

    async def close(code: int) -> None:
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(RuntimeError):
                await websocket.close(code)

    kp: KpCache = websocket.app.state.kp
    conn = Connection(info.device_id, send_text, close)
    old = await registry.attach(conn)
    if old is not None:
        await old.close(CloseCode.REPLACED)
    await _touch(
        maker, info.device_id, offline_notified_at=None, offline_mailed_at=None
    )  # a new outage may notify
    log.info("device connected", extra={"device_id": info.device_id})

    async def expire() -> None:
        await asyncio.sleep(max(0.0, (info.expires_at - _now()).total_seconds()))
        await close(CloseCode.REAUTH)

    watchdog = asyncio.create_task(expire())
    bucket = _Bucket()
    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > MAX_MESSAGE_CHARS:
                log.warning("message too large", extra={"device_id": info.device_id})
                continue
            await bucket.take()  # a flooding device is slowed down, nothing is lost
            await _handle(raw, conn, maker, settings, registry, notifier, store, kp)
    except WebSocketDisconnect:
        pass
    finally:
        watchdog.cancel()
        await registry.detach(conn)
        log.info("device disconnected", extra={"device_id": info.device_id})


# No protocol message comes near this; a status with its schema is a few kB.
MAX_MESSAGE_CHARS = 256_000
# Messages a device may send in a burst (a night of backlog: products and hundreds of
# events) and per second after that.
BURST = 1000
PER_SECOND = 50.0


class _Bucket:
    """Token bucket per connection: one compromised device cannot flood the database."""

    def __init__(self) -> None:
        self._tokens = float(BURST)
        self._at = time.monotonic()

    async def take(self) -> None:
        now = time.monotonic()
        self._tokens = min(BURST, self._tokens + (now - self._at) * PER_SECOND)
        self._at = now
        if self._tokens < 1:
            await asyncio.sleep((1 - self._tokens) / PER_SECOND)
            self._tokens = 1.0
            self._at = time.monotonic()
        self._tokens -= 1


# A chart point every five minutes is plenty; status comes about every minute.
STATUS_SAMPLE_EVERY = dt.timedelta(minutes=5)


async def _store_status(
    maker: async_sessionmaker[AsyncSession], device_id: str, status: Status
) -> None:
    """Keep the camera's state for the charts (exposure, gain, temperatures, disk)."""
    now = _now()
    async with maker() as db:
        last = await db.scalar(
            select(func.max(StatusSample.at)).where(StatusSample.device_id == device_id)
        )
        if last is not None and now - last < STATUS_SAMPLE_EVERY:
            return
        db.add(
            StatusSample(
                device_id=device_id,
                at=now,
                mode=status.mode.value,
                exposure_us=status.exposure_us,
                gain=status.gain,
                sensor_temp_c=status.sensor_temp_c,
                cpu_temp_c=status.cpu_temp_c,
                disk_free_pct=status.disk_free_pct,
            )
        )
        await db.commit()


async def _safe(maker: async_sessionmaker[AsyncSession], device_id: str) -> bool | None:
    """The last reported ``status.observatory.safe`` (SPEC §6.3), or None."""
    async with maker() as db:
        status = await db.scalar(select(Device.last_status).where(Device.id == device_id))
    observatory: dict[str, Any] = (status or {}).get("observatory") or {}
    safe = observatory.get("safe")
    return safe if isinstance(safe, bool) else None


async def _touch(maker: async_sessionmaker[AsyncSession], device_id: str, **values: Any) -> None:
    async with maker() as db:
        await db.execute(
            update(Device).where(Device.id == device_id).values(last_seen_at=_now(), **values)
        )
        await db.commit()


async def _handle(
    raw: str,
    conn: Connection,
    maker: async_sessionmaker[AsyncSession],
    settings: Settings,
    registry: ConnectionRegistry,
    notifier: Notifier,
    store: ImageStore,
    kp: KpCache,
) -> None:
    try:
        env = parse_envelope(raw)
    except ValidationError:
        log.warning("invalid message from device", extra={"device_id": conn.device_id})
        return
    body = env.body
    if isinstance(body, Hello):
        if body.device_id != conn.device_id:
            await conn.close(CloseCode.REAUTH)
            return
        values: dict[str, Any] = {"profile": body.profile, "agent_version": body.agent_version}
        if body.settings_schema:
            # SPEC §6.9: keep the newest consistent schema; an inconsistent one is ignored.
            schema = [s.model_dump(mode="json") for s in body.settings_schema]
            if settings_form.parse_schema(schema) is not None:
                values["settings_schema"] = schema
            else:
                log.warning("inconsistent settings schema", extra={"device_id": conn.device_id})
        await _touch(maker, conn.device_id, **values)
        # SPEC §6.5 local_access: resend an unacknowledged verifier after every hello. In a
        # task: the reply arrives on this very connection.
        _background(local_access.deliver(maker, registry, conn.device_id))
        # SPEC §6.4 Labels: labels set on the hub until the camera acknowledges them.
        _background(labels.deliver(maker, registry, conn.device_id))
    elif isinstance(body, Status):
        before = await _safe(maker, conn.device_id)
        await _touch(maker, conn.device_id, last_status=body.model_dump(mode="json"))
        await _store_status(maker, conn.device_id, body)
        # Roadmap C15: tell the owner when the sky turns unsafe for the telescope.
        if before is True and body.observatory is not None and body.observatory.safe is False:
            _background(notifier.unsafe(conn.device_id))
        if body.sky is not None:
            await _store_sky(maker, conn.device_id, body.sky)
            _background(notifier.aurora(conn.device_id, kp))
    elif isinstance(body, FrameInfo):
        await _on_frame(body, conn, maker, settings, registry)
    elif isinstance(body, Products):
        await _on_products(body, conn, maker, registry)
    elif isinstance(body, Event):
        await _on_event(body, conn, maker, registry, notifier, store)
    elif isinstance(body, EventLabel):
        async with maker() as db:
            await labels.from_device(db, conn.device_id, body)
    elif isinstance(body, Aircraft):
        # SPEC §6.3, §7.8: the owner's receiver; kept for the overlay and the match only.
        if aircraft.current is not None:
            aircraft.current.receive(conn.device_id, body.at, body.aircraft)
    elif isinstance(body, NightDeleted):
        async with maker() as db:
            await labels.delete_night(db, store, conn.device_id, body.night_id)
    elif isinstance(body, Ack | ErrorReply):
        registry.resolve(conn.device_id, body)
        if isinstance(body, ErrorReply):
            log.info(
                "device command failed",
                extra={"device_id": conn.device_id, "code": body.code, "ref": body.ref},
            )
    else:
        log.info("ignored message", extra={"device_id": conn.device_id, "type": env.type})


async def _on_frame(
    frame: FrameInfo,
    conn: Connection,
    maker: async_sessionmaker[AsyncSession],
    settings: Settings,
    registry: ConnectionRegistry,
) -> None:
    """SPEC §6.5: the hub decides what to fetch.

    * full image: every frame while someone watches live, otherwise at most every
      ``latest_image_interval_s``;
    * thumbnail (gallery): at most every ``thumb_interval_s`` and with every full image.

    A bad frame (SPEC §4.9, too dark or too bright) is fetched only for the live view and
    never as a thumbnail, so it stays out of the gallery, the loop and everything made from
    thumbnails (renders, month keograms), and out of the latest image between live views.

    Every requested frame gets a ``Frame`` row with its metadata; uploads fill it in."""
    now = _now()
    async with maker() as db:
        device = await db.get(Device, conn.device_id)
        if device is None:  # pragma: no cover - deleted while connected
            return
        device.last_frame = frame.model_dump(mode="json")
        device.last_seen_at = now
        full_due = device.latest_image_at is None or now - device.latest_image_at >= (
            dt.timedelta(seconds=settings.latest_image_interval_s)
        )
        thumb_due = device.latest_thumb_at is None or now - device.latest_thumb_at >= (
            dt.timedelta(seconds=settings.thumb_interval_s)
        )
        bad = frame.bad is not None
        want_full = (full_due and not bad) or registry.is_live(conn.device_id)
        want_thumb = (thumb_due or want_full) and not bad
        if want_thumb or want_full:
            await db.execute(
                pg_insert(Frame)
                .values(
                    device_id=conn.device_id,
                    night_id=frame.night_id,
                    name=frame.name,
                    captured_at=frame.captured_at,
                    mode=frame.mode.value,
                    exposure_us=frame.exposure_us,
                    gain=frame.gain,
                    mean=frame.mean,
                    sun_elevation=frame.sun_elevation,
                )
                .on_conflict_do_nothing(index_elements=["device_id", "night_id", "name"])
            )
        await db.commit()
    if want_full:
        await registry.request_upload(conn.device_id, frame.night_id, frame.name, FrameVariant.FULL)
    if want_thumb:
        await registry.request_upload(
            conn.device_id, frame.night_id, frame.name, FrameVariant.THUMB
        )


# Keograms and startrails are a few MB: fetched in full right away. Timelapses (up to
# hundreds of MB) only when someone opens them (app API, ``request_product``).
FETCH_AT_ONCE = frozenset({ProductKind.KEOGRAM, ProductKind.STARTRAILS, ProductKind.DAY_KEOGRAM})
FETCH_AT_ONCE_MAX = 50 * 1024 * 1024


async def _on_products(
    body: Products,
    conn: Connection,
    maker: async_sessionmaker[AsyncSession],
    registry: ConnectionRegistry,
) -> None:
    """SPEC §6.3: record the night's products and fetch thumbnails and small products.

    A product that was rebuilt (other size) is fetched again."""
    wanted: list[tuple[str, FrameVariant]] = []
    oldest = (dt.datetime.now(dt.UTC) - dt.timedelta(days=public.PUBLIC_VIDEO_DAYS)).strftime(
        "%Y%m%d"
    )
    recent = body.night_id >= oldest
    async with maker() as db:
        device = await db.get(Device, conn.device_id)
        public_video = device is not None and public.shows_videos(device)
        for item in body.products:
            row = await db.scalar(
                select(Product).where(
                    Product.device_id == conn.device_id,
                    Product.night_id == body.night_id,
                    Product.name == item.name,
                )
            )
            if row is None:
                row = Product(
                    device_id=conn.device_id,
                    night_id=body.night_id,
                    name=item.name,
                    kind=item.kind.value,
                    content_type=item.content_type,
                    size=item.size,
                    duration_s=item.duration_s,
                    thumbnail=item.thumbnail,
                )
                db.add(row)
            elif row.size != item.size:
                row.size, row.duration_s, row.thumbnail = item.size, item.duration_s, item.thumbnail
                row.has_full = row.has_thumb = False
                row.full_at = None
            if item.thumbnail and not row.has_thumb:
                wanted.append((item.name, FrameVariant.THUMB))
            if item.kind in FETCH_AT_ONCE and item.size <= FETCH_AT_ONCE_MAX and not row.has_full:
                wanted.append((item.name, FrameVariant.FULL))
            elif (
                item.kind is ProductKind.TIMELAPSE and not row.has_full and recent and public_video
            ):
                # Roadmap #16: the public page shows last night's timelapse like Allsky's
                # website; only recent nights, so an import does not upload years of video.
                wanted.append((item.name, FrameVariant.FULL))
        await db.commit()
    for name, variant in wanted:
        await registry.request_product(conn.device_id, body.night_id, name, variant)


DATA_KEYS = 64
DATA_TEXT = 200


def _bounded(data: dict[str, Any]) -> dict[str, Any]:
    """SPEC §6.4 data from a device, within sane bounds: the protocol allows any number of
    keys and any string length, the database should not."""
    out: dict[str, Any] = {}
    for key, value in list(data.items())[:DATA_KEYS]:
        out[key[:DATA_TEXT]] = value[:DATA_TEXT] if isinstance(value, str) else value
    return out


async def _on_event(
    body: Event,
    conn: Connection,
    maker: async_sessionmaker[AsyncSession],
    registry: ConnectionRegistry,
    notifier: Notifier,
    store: ImageStore,
) -> None:
    """SPEC §6.4: upsert by the device's stable id (events are resent after a reconnect);
    fetch the thumbnail right away, the full picture when someone opens it (or right away
    for owners who keep events longer than the default)."""
    values = {
        "night_id": body.night_id,
        "kind": body.kind.value,
        "start": body.start,
        "end": body.end,
        "confidence": body.confidence,
        "has_image": body.has_image,
        # SPEC §6.4 imported: kept with the data, where push and Discord look for it.
        "data": _bounded(dict(body.data)) | ({"imported": True} if body.imported else {}),
    }
    async with maker() as db:
        if not await _still_paired(db, conn.device_id):
            return
        keep_days = await db.scalar(
            select(User.event_keep_days)
            .join(Device, Device.owner_id == User.id)
            .where(Device.id == conn.device_id)
        )
        previous = await db.scalar(
            select(EventRecord.data).where(
                EventRecord.device_id == conn.device_id, EventRecord.event_id == body.id
            )
        )
        is_new = previous is None
        # SPEC §6.4 image_rev: a higher revision means the device replaced the picture.
        replaced = not is_new and image_rev(body.data) > image_rev(previous or {})
        update_values: dict[str, Any] = dict(values)
        if replaced:
            update_values |= {"has_thumb": False, "has_full": False}
        await db.execute(
            pg_insert(EventRecord)
            .values(device_id=conn.device_id, event_id=body.id, **values)
            .on_conflict_do_update(index_elements=["device_id", "event_id"], set_=update_values)
        )
        stored = (
            await db.execute(
                select(EventRecord.has_thumb, EventRecord.has_full).where(
                    EventRecord.device_id == conn.device_id, EventRecord.event_id == body.id
                )
            )
        ).one()
        await db.commit()
    if replaced:
        # Never serve (or let browsers cache) the old picture under the new revision.
        for variant in FrameVariant:
            await asyncio.to_thread(
                store.delete_event, conn.device_id, body.night_id, body.id, variant
            )
    if is_new:
        # Roadmap #6; in its own task so a slow push service never delays the device.
        _background(notifier.event(conn.device_id, body.id))
        if notifier.discord is not None:
            _background(notifier.discord.event(conn.device_id, body.id, picture_arrived=False))
    if body.kind == "meteor":
        # Roadmap C4: the same meteor at another camera (also when the event was updated).
        _background(meteor_pairs.pair_event(maker, conn.device_id, body.id))
        # Roadmap H6 (SPEC §7.9): a satellite, or an aircraft already covered (§7.8)?
        if aircraft.current is not None:
            _background(aircraft.current.check_new(conn.device_id, body.id))
    if not body.has_image:
        return
    if not stored.has_thumb:
        await registry.request_event(conn.device_id, body.night_id, body.id, FrameVariant.THUMB)
    # Owners who keep events longer keep them in full: fetch the picture while it exists
    # (again when it was replaced, so the kept picture is the final one).
    if (keep_days or DEFAULT_EVENT_KEEP_DAYS) > DEFAULT_EVENT_KEEP_DAYS and not stored.has_full:
        await registry.request_event(conn.device_id, body.night_id, body.id, FrameVariant.FULL)


_tasks: set[asyncio.Task[Any]] = set()


def _background(coro: Coroutine[Any, Any, Any]) -> None:
    """Fire and forget, keeping a reference until done (asyncio drops unreferenced tasks)."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _store_sky(
    maker: async_sessionmaker[AsyncSession], device_id: str, sky: SkyMetrics
) -> None:
    """SPEC §6.3 ``status.sky``: one row per measured frame (the device repeats the last
    measurement until a new frame comes, so duplicates of ``at`` are skipped)."""
    async with maker() as db:
        if not await _still_paired(db, device_id):
            return
        await db.execute(
            pg_insert(SkySample)
            .values(
                device_id=device_id,
                night_id=sky.night_id,
                at=sky.at,
                cloud_cover=sky.cloud_cover,
                sqm_mag=sky.sqm_mag,
                stars=sky.stars,
            )
            .on_conflict_do_nothing(index_elements=["device_id", "at"])
        )
        await db.commit()


async def _still_paired(db: AsyncSession, device_id: str) -> bool:
    """Data that arrives after the camera was removed is dropped: a later owner must never see
    it. The row lock makes a concurrent unpairing wait for this transaction (or this one see
    its result)."""
    owner = await db.scalar(
        select(Device.owner_id).where(Device.id == device_id).with_for_update(read=True)
    )
    return owner is not None
