"""Device API v1 (SPEC §6.1, §6.6): challenge, register, token, WebSocket, uploads."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Request, WebSocket
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocketDisconnect, WebSocketState

from allskyhub_protocol import (
    Ack,
    ChallengeRequest,
    ChallengeResponse,
    CloseCode,
    ErrorReply,
    FrameInfo,
    FrameVariant,
    Hello,
    RegisterRequest,
    RegisterResponse,
    Status,
    TokenRequest,
    TokenResponse,
    parse_envelope,
)
from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import pairing
from allskyhub_server.devices.connections import Connection, ConnectionRegistry
from allskyhub_server.devices.images import JPEG_MAGIC, ImageStore
from allskyhub_server.models import Device, Frame
from allskyhub_server.settings import Settings
from allskyhub_server.web.deps import DbSession, SettingsDep, client_ip

log = logging.getLogger(__name__)
router = APIRouter(prefix="/device/v1")

NIGHT_ID = r"^\d{8}$"
FRAME_NAME = r"^[A-Za-z0-9._-]{1,128}$"


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
    return TokenResponse(access_token=issued.token, expires_in=issued.expires_in)


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
    if not registry.take_upload(info.device_id, night_id, name, variant):
        raise HTTPException(404, "Upload was not requested")  # taken by a parallel upload
    store: ImageStore = request.app.state.images
    await asyncio.to_thread(store.save_frame, info.device_id, night_id, name, variant, bytes(data))
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


# --- WebSocket (SPEC §6.1, §6.3, §6.5) ------------------------------------------------------


@router.websocket("/ws")
async def device_ws(websocket: WebSocket) -> None:
    app = websocket.app
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    settings: Settings = app.state.settings
    registry: ConnectionRegistry = app.state.connections

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

    conn = Connection(info.device_id, send_text, close)
    old = await registry.attach(conn)
    if old is not None:
        await old.close(CloseCode.REPLACED)
    await _touch(maker, info.device_id)
    log.info("device connected", extra={"device_id": info.device_id})

    async def expire() -> None:
        await asyncio.sleep(max(0.0, (info.expires_at - _now()).total_seconds()))
        await close(CloseCode.REAUTH)

    watchdog = asyncio.create_task(expire())
    try:
        while True:
            raw = await websocket.receive_text()
            await _handle(raw, conn, maker, settings, registry)
    except WebSocketDisconnect:
        pass
    finally:
        watchdog.cancel()
        await registry.detach(conn)
        log.info("device disconnected", extra={"device_id": info.device_id})


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
        await _touch(maker, conn.device_id, profile=body.profile, agent_version=body.agent_version)
    elif isinstance(body, Status):
        await _touch(maker, conn.device_id, last_status=body.model_dump(mode="json"))
    elif isinstance(body, FrameInfo):
        await _on_frame(body, conn, maker, settings, registry)
    elif isinstance(body, Ack | ErrorReply):
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
        want_full = full_due or registry.is_live(conn.device_id)
        want_thumb = thumb_due or want_full
        if want_thumb:
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
