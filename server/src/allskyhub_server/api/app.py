"""App API v1 (M3): bearer-token login, cameras, claiming by code, images.

Only bearer tokens, never cookies, so no CSRF protection is needed. The pairing flow of
SPEC §6.2 step 3: the app reads the code from the camera's local setup API and posts it
to ``/api/v1/cameras/claim``.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from allskyhub_protocol import CloseCode, FrameVariant
from allskyhub_server.auth import accounts, app_tokens, ratelimit
from allskyhub_server.devices import pairing, queries
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, User
from allskyhub_server.web.deps import DbSession, SettingsDep, client_ip

router = APIRouter(prefix="/api/v1")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(_In):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)
    # Name of the phone, e.g. "Pixel 8".
    label: str = Field(default="", max_length=100)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105


class ClaimRequest(_In):
    code: str = Field(max_length=20)
    name: str = Field(default="", max_length=100)


class Camera(BaseModel):
    id: str
    name: str
    profile: str
    agent_version: str
    online: bool
    paired_at: dt.datetime | None
    last_seen_at: dt.datetime | None
    latest_image_at: dt.datetime | None
    # Latest `status` and `frame` bodies as the camera sent them (SPEC §6.3).
    status: dict[str, Any] | None
    frame: dict[str, Any] | None


def _bearer(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise HTTPException(401, "Missing bearer token", {"WWW-Authenticate": "Bearer"})
    return value.strip()


async def current_user(request: Request, db: DbSession) -> User:
    user = await app_tokens.user_for(db, _bearer(request))
    if user is None:
        raise HTTPException(401, "Invalid token", {"WWW-Authenticate": "Bearer"})
    return user


AppUser = Annotated[User, Depends(current_user)]


def _registry(request: Request) -> ConnectionRegistry:
    registry: ConnectionRegistry = request.app.state.connections
    return registry


def _camera(device: Device, registry: ConnectionRegistry) -> Camera:
    return Camera(
        id=device.id,
        name=device.name,
        profile=device.profile,
        agent_version=device.agent_version,
        online=registry.is_online(device.id),
        paired_at=device.paired_at,
        last_seen_at=device.last_seen_at,
        latest_image_at=device.latest_image_at,
        status=device.last_status,
        frame=device.last_frame,
    )


async def _limit(request: Request, key: str, limits: tuple[ratelimit.Limit, ...]) -> None:
    try:
        await ratelimit.hit(request.app.state.engine, key, limits)
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Too many requests", {"Retry-After": str(exc.retry_after)}
        ) from None


@router.post("/auth/login")
async def login(
    request: Request, body: LoginRequest, db: DbSession, settings: SettingsDep
) -> LoginResponse:
    await _limit(request, f"login:ip:{client_ip(request, settings)}", ratelimit.LOGIN_PER_IP)
    await _limit(
        request, f"login:acct:{accounts.normalize_email(body.email)}", ratelimit.LOGIN_PER_ACCOUNT
    )
    user = await accounts.authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(401, "Wrong email or password")
    token = await app_tokens.issue(db, user.id, body.label)
    await db.commit()
    return LoginResponse(access_token=token)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, db: DbSession, user: AppUser) -> Response:
    await app_tokens.revoke(db, _bearer(request))
    return Response(status_code=204)


@router.get("/cameras")
async def cameras(request: Request, db: DbSession, user: AppUser) -> list[Camera]:
    registry = _registry(request)
    return [_camera(d, registry) for d in await queries.owned(db, user.id)]


@router.post("/cameras/claim")
async def claim(
    request: Request, body: ClaimRequest, db: DbSession, settings: SettingsDep, user: AppUser
) -> Camera:
    """SPEC §6.2 step 3. 400 for unknown, expired and used codes alike; 409 if the camera
    belongs to another account."""
    await _limit(request, f"claim:user:{user.id}", ratelimit.CLAIM_PER_USER)
    await _limit(request, f"claim:ip:{client_ip(request, settings)}", ratelimit.CLAIM_PER_IP)
    try:
        device = await pairing.claim(db, user.id, body.code, body.name)
    except pairing.PairedElsewhereError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from None
    except pairing.PairingError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from None
    await db.commit()
    return _camera(device, _registry(request))


async def _own(db: DbSession, user: User, device_id: str) -> Device:
    device = await queries.owned_one(db, user.id, device_id)
    if device is None:
        raise HTTPException(404, "Not found")
    return device


@router.get("/cameras/{device_id}")
async def camera(request: Request, db: DbSession, user: AppUser, device_id: str) -> Camera:
    return _camera(await _own(db, user, device_id), _registry(request))


@router.get("/cameras/{device_id}/image/{variant}.jpg")
async def image(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    variant: FrameVariant,
    live: bool = False,
) -> Response:
    """``live=true`` while the app shows the live view: the hub then asks for every frame
    (SPEC §6.5). Poll it every few seconds."""
    device = await _own(db, user, device_id)
    if live:
        _registry(request).touch_live(device.id)
    store: ImageStore = request.app.state.images
    path = store.path(device.id, variant)
    if not path.is_file():
        raise HTTPException(404, "No image yet")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.delete("/cameras/{device_id}", status_code=204)
async def remove(request: Request, db: DbSession, user: AppUser, device_id: str) -> Response:
    """SPEC §6.2 step 4."""
    device = await _own(db, user, device_id)
    await pairing.unpair(db, device)
    await db.commit()
    store: ImageStore = request.app.state.images
    store.delete_device(device.id)
    await _registry(request).close(device.id, CloseCode.UNPAIRED)
    return Response(status_code=204)
