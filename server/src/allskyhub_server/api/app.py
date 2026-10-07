"""App API v1 (M3): bearer-token login, cameras, claiming by code, images.

Only bearer tokens, never cookies, so no CSRF protection is needed. The pairing flow of
SPEC §6.2 step 3: the app reads the code from the camera's local setup API and posts it
to ``/api/v1/cameras/claim``.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from allskyhub_protocol import CloseCode, FrameVariant
from allskyhub_server.auth import accounts, app_tokens, ratelimit
from allskyhub_server.devices import pairing, queries
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, Frame, Product, User
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


class Night(BaseModel):
    night_id: str
    frames: int
    first: dt.datetime | None
    last: dt.datetime | None
    # Night products announced for this night (SPEC §5.2).
    products: int = 0


class ProductItem(BaseModel):
    """A night product (SPEC §5.2, §6.3). ``pending``: the hub has asked the camera for the
    full file and is waiting for it."""

    kind: str
    name: str
    content_type: str
    size: int
    duration_s: float | None
    has_full: bool
    has_thumb: bool
    pending: bool


class FrameItem(BaseModel):
    """A frame in the archive (SPEC §4.4); ``has_full`` is false once the full image aged
    out (``keep_full_days``) or was not requested."""

    name: str
    captured_at: dt.datetime
    mode: str
    exposure_us: int
    gain: float
    sun_elevation: float
    has_full: bool


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


NIGHT_ID = r"^\d{8}$"
FRAME_NAME = r"^[A-Za-z0-9._-]{1,128}$"


@router.get("/cameras/{device_id}/nights")
async def nights(db: DbSession, user: AppUser, device_id: str) -> list[Night]:
    """Nights with archived frames or products, newest first."""
    device = await _own(db, user, device_id)
    by_night: dict[str, Night] = {}
    rows = await db.execute(
        select(
            Frame.night_id, func.count(), func.min(Frame.captured_at), func.max(Frame.captured_at)
        )
        .where(Frame.device_id == device.id, Frame.has_thumb.is_(True))
        .group_by(Frame.night_id)
    )
    for night_id, count, first, last in rows:
        by_night[night_id] = Night(night_id=night_id, frames=count, first=first, last=last)
    products = await db.execute(
        select(Product.night_id, func.count())
        .where(Product.device_id == device.id)
        .group_by(Product.night_id)
    )
    for night_id, count in products:
        night = by_night.setdefault(
            night_id, Night(night_id=night_id, frames=0, first=None, last=None)
        )
        night.products = count
    return sorted(by_night.values(), key=lambda n: n.night_id, reverse=True)


@router.get("/cameras/{device_id}/nights/{night_id}/frames")
async def frames(
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=NIGHT_ID)],
) -> list[FrameItem]:
    device = await _own(db, user, device_id)
    rows = await db.scalars(
        select(Frame)
        .where(Frame.device_id == device.id, Frame.night_id == night_id, Frame.has_thumb.is_(True))
        .order_by(Frame.captured_at)
    )
    return [
        FrameItem(
            name=f.name,
            captured_at=f.captured_at,
            mode=f.mode,
            exposure_us=f.exposure_us,
            gain=f.gain,
            sun_elevation=f.sun_elevation,
            has_full=f.has_full,
        )
        for f in rows
    ]


@router.get("/cameras/{device_id}/frames/{night_id}/{name}/{variant}.jpg")
async def frame_image(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=NIGHT_ID)],
    name: Annotated[str, Path(pattern=FRAME_NAME)],
    variant: FrameVariant,
) -> Response:
    device = await _own(db, user, device_id)
    store: ImageStore = request.app.state.images
    path = store.frame_path(device.id, night_id, name, variant)
    if not path.is_file():
        raise HTTPException(404, "Not found")
    # Archived images never change: the app may cache them.
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"}
    )


PRODUCT_NAME = r"^(keogram\.jpg|startrails\.jpg|timelapse\.mp4)$"


@router.get("/cameras/{device_id}/nights/{night_id}/products")
async def products(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=NIGHT_ID)],
) -> list[ProductItem]:
    device = await _own(db, user, device_id)
    registry = _registry(request)
    rows = await db.scalars(
        select(Product)
        .where(Product.device_id == device.id, Product.night_id == night_id)
        .order_by(Product.kind)
    )
    return [
        ProductItem(
            kind=p.kind,
            name=p.name,
            content_type=p.content_type,
            size=p.size,
            duration_s=p.duration_s,
            has_full=p.has_full,
            has_thumb=p.has_thumb,
            pending=registry.is_pending(device.id, night_id, p.name, FrameVariant.FULL, "product"),
        )
        for p in rows
    ]


@router.get("/cameras/{device_id}/products/{night_id}/{name}", response_model=None)
async def product_file(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=NIGHT_ID)],
    name: Annotated[str, Path(pattern=PRODUCT_NAME)],
    variant: FrameVariant = FrameVariant.FULL,
) -> Response:
    """The product file (videos with HTTP range requests, so players can seek).

    If the hub does not have the full file yet, it asks the camera for it and answers
    ``202 {"status": "requested"}``; poll again. 404 when the camera is offline."""
    device = await _own(db, user, device_id)
    product = await db.scalar(
        select(Product).where(
            Product.device_id == device.id, Product.night_id == night_id, Product.name == name
        )
    )
    if product is None:
        raise HTTPException(404, "Not found")
    store: ImageStore = request.app.state.images
    path = store.product_path(device.id, night_id, name, variant)
    if path.is_file():
        media = product.content_type if variant is FrameVariant.FULL else "image/jpeg"
        return FileResponse(
            path, media_type=media, headers={"Cache-Control": "private, max-age=86400"}
        )
    registry = _registry(request)
    if variant is FrameVariant.FULL and (
        registry.is_pending(device.id, night_id, name, variant, "product")
        or await registry.request_product(device.id, night_id, name, variant)
    ):
        return JSONResponse({"status": "requested"}, status_code=202)
    raise HTTPException(404, "Not available")
