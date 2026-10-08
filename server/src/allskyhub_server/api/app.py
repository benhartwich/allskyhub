"""App API v1 (M3): bearer-token login, cameras, claiming by code, images.

Only bearer tokens, never cookies, so no CSRF protection is needed. The pairing flow of
SPEC §6.2 step 3: the app reads the code from the camera's local setup API and posts it
to ``/api/v1/cameras/claim``.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from allskyhub_protocol import EVENT_ID_PATTERN, CloseCode, FrameVariant, SetSettingsArgs
from allskyhub_server.auth import accounts, app_tokens, ratelimit
from allskyhub_server.auth.passwords import verify_secret_async
from allskyhub_server.auth.tokens import hash_token
from allskyhub_server.devices import archive, events, pairing, public, queries
from allskyhub_server.devices import settings as settings_mod
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, EventRecord, PushToken, User
from allskyhub_server.web.deps import DbSession, SettingsDep, client_ip
from allskyhub_server.web.routes_account import finish_deletion

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


class PasswordChange(_In):
    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


class AccountSettings(BaseModel):
    """Per-account settings: days to keep detections (30, 90, 365) and push notifications
    (roadmap #6). Fields left out of a PUT stay as they are."""

    event_keep_days: int | None = None
    notify_events: bool | None = None
    notify_offline: bool | None = None
    # Read only: the hub can send push notifications at all.
    push_available: bool | None = None


class PushTokenRequest(_In):
    token: str = Field(min_length=10, max_length=4096)
    platform: Literal["android", "ios"]


class AccountDelete(_In):
    password: str = Field(max_length=1024)


class PublicRequest(_In):
    enabled: bool


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
    # Detections reported for this night (SPEC §6.4).
    events: int = 0


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


class EventItem(BaseModel):
    """A detection (SPEC §6.4). ``data`` keys per kind, e.g. for meteors ``length_px``,
    ``peak``, ``frames``, ``direction_deg``, ``shower``."""

    id: str
    night_id: str
    kind: str
    start: dt.datetime
    end: dt.datetime
    confidence: float
    has_image: bool
    has_thumb: bool
    has_full: bool
    data: dict[str, Any]
    # The owner's verdict: "confirmed", "false_positive" or None.
    label: str | None = None
    # SPEC §6.4: revision of the picture; put it into image URLs (``v``) so caches renew.
    image_rev: int = 1


class LabelRequest(_In):
    label: Literal["confirmed", "false_positive"] | None


def _event(e: EventRecord) -> EventItem:
    return EventItem(
        id=e.event_id,
        night_id=e.night_id,
        kind=e.kind,
        start=e.start,
        end=e.end,
        confidence=e.confidence,
        has_image=e.has_image,
        has_thumb=e.has_thumb,
        has_full=e.has_full,
        data=e.data,
        label=e.label,
        image_rev=events.image_rev(e.data),
    )


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
    # Opt-in public sky page (roadmap #9), or None.
    public_url: str | None = None


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


def _camera(device: Device, registry: ConnectionRegistry, base_url: str) -> Camera:
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
        public_url=(
            f"{base_url.rstrip('/')}/sky/{device.public_slug}" if device.public_slug else None
        ),
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
    return [
        _camera(d, registry, request.app.state.settings.base_url)
        for d in await queries.owned(db, user.id)
    ]


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
    return _camera(device, _registry(request), request.app.state.settings.base_url)


async def _own(db: DbSession, user: User, device_id: str) -> Device:
    device = await queries.owned_one(db, user.id, device_id)
    if device is None:
        raise HTTPException(404, "Not found")
    return device


@router.get("/cameras/{device_id}")
async def camera(request: Request, db: DbSession, user: AppUser, device_id: str) -> Camera:
    device = await _own(db, user, device_id)
    return _camera(device, _registry(request), request.app.state.settings.base_url)


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


@router.get("/cameras/{device_id}/nights")
async def nights(db: DbSession, user: AppUser, device_id: str) -> list[Night]:
    """Nights with archived frames or products, newest first."""
    device = await _own(db, user, device_id)
    return [
        Night(
            night_id=n.night_id,
            frames=n.frames,
            first=n.first,
            last=n.last,
            products=n.products,
            events=n.events,
        )
        for n in await archive.nights(db, device.id)
    ]


@router.get("/cameras/{device_id}/nights/{night_id}/frames")
async def frames(
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=archive.NIGHT_ID)],
) -> list[FrameItem]:
    device = await _own(db, user, device_id)
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
        for f in await archive.night_frames(db, device.id, night_id)
    ]


@router.get("/cameras/{device_id}/frames/{night_id}/{name}/{variant}.jpg")
async def frame_image(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=archive.NIGHT_ID)],
    name: Annotated[str, Path(pattern=archive.FRAME_NAME)],
    variant: FrameVariant,
) -> Response:
    device = await _own(db, user, device_id)
    return archive.frame_file(request.app.state.images, device.id, night_id, name, variant)


@router.get("/cameras/{device_id}/nights/{night_id}/products")
async def products(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=archive.NIGHT_ID)],
) -> list[ProductItem]:
    device = await _own(db, user, device_id)
    registry = _registry(request)
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
        for p in await archive.night_products(db, device.id, night_id)
    ]


@router.get("/cameras/{device_id}/products/{night_id}/{name}", response_model=None)
async def product_file(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=archive.NIGHT_ID)],
    name: Annotated[str, Path(pattern=archive.PRODUCT_NAME)],
    variant: FrameVariant = FrameVariant.FULL,
) -> Response:
    """See ``archive.product_file``: the file, or 202 while the hub fetches it."""
    device = await _own(db, user, device_id)
    return await archive.product_file(
        db, request.app.state.images, _registry(request), device.id, night_id, name, variant
    )


@router.get("/cameras/{device_id}/events")
async def camera_events(
    db: DbSession,
    user: AppUser,
    device_id: str,
    before: dt.datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    hide_false: bool = False,
) -> list[EventItem]:
    """Newest detections across nights; page with ``before`` (the last item's ``start``);
    ``hide_false`` leaves out the ones marked as false positives."""
    device = await _own(db, user, device_id)
    rows = await events.events(db, device.id, before=before, limit=limit, hide_false=hide_false)
    return [_event(e) for e in rows]


@router.get("/cameras/{device_id}/nights/{night_id}/events")
async def night_events(
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=archive.NIGHT_ID)],
) -> list[EventItem]:
    device = await _own(db, user, device_id)
    rows = await events.events(db, device.id, night_id=night_id, limit=1000)
    return [_event(e) for e in rows]


@router.put("/cameras/{device_id}/settings")
async def camera_settings(
    request: Request, body: SetSettingsArgs, db: DbSession, user: AppUser, device_id: str
) -> dict[str, str]:
    """SPEC §6.5 ``set_settings`` (roadmap #2): only the given keys change. Waits for the
    camera's answer; 409 offline, 400 rejected (German message), 504 no answer. The camera
    then restarts its capture and reports the new values in ``status.settings``."""
    device = await _own(db, user, device_id)
    outcome = await settings_mod.apply(_registry(request), device.id, body)
    if not outcome.ok:
        raise HTTPException(outcome.status, outcome.message)
    return {"status": "applied"}


@router.get("/cameras/{device_id}/events/export")
async def export_events(
    db: DbSession, user: AppUser, device_id: str, labelled_only: bool = False
) -> list[dict[str, Any]]:
    """All events with data and the owner's labels, for tuning the detector."""
    device = await _own(db, user, device_id)
    rows = await events.events(db, device.id, limit=100_000)
    return [events.export_row(e) for e in rows if e.label or not labelled_only]


@router.put("/cameras/{device_id}/events/{night_id}/{event_id}/label")
async def label_event(
    body: LabelRequest,
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=archive.NIGHT_ID)],
    event_id: Annotated[str, Path(pattern=EVENT_ID_PATTERN)],
) -> EventItem:
    """The owner's verdict, e.g. "false_positive" for "kein Meteor"; null clears it."""
    device = await _own(db, user, device_id)
    event = await events.set_label(db, device.id, night_id, event_id, body.label)
    await db.commit()
    return _event(event)


@router.get("/cameras/{device_id}/events/{night_id}/{event_id}/image", response_model=None)
async def event_image(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    night_id: Annotated[str, Path(pattern=archive.NIGHT_ID)],
    event_id: Annotated[str, Path(pattern=EVENT_ID_PATTERN)],
    variant: FrameVariant = FrameVariant.FULL,
) -> Response:
    """The event's picture, or 202 while the hub fetches the full one from the camera."""
    device = await _own(db, user, device_id)
    return await events.event_file(
        db, request.app.state.images, _registry(request), device.id, night_id, event_id, variant
    )


async def _limit_account(request: Request, user: User) -> None:
    await _limit(request, f"account:{user.id}", ratelimit.LOGIN_PER_ACCOUNT)


def _account_settings(request: Request, user: User) -> AccountSettings:
    return AccountSettings(
        event_keep_days=user.event_keep_days,
        notify_events=user.notify_events,
        notify_offline=user.notify_offline,
        push_available=request.app.state.notifier.enabled,
    )


@router.get("/account/settings")
async def get_settings(request: Request, user: AppUser) -> AccountSettings:
    return _account_settings(request, user)


@router.put("/account/settings")
async def put_settings(
    request: Request, body: AccountSettings, db: DbSession, user: AppUser
) -> AccountSettings:
    try:
        if body.event_keep_days is not None:
            accounts.set_event_keep_days(user, body.event_keep_days)
    except accounts.AccountError as exc:
        raise HTTPException(400, exc.message) from None
    if body.notify_events is not None:
        user.notify_events = body.notify_events
    if body.notify_offline is not None:
        user.notify_offline = body.notify_offline
    await db.commit()
    return _account_settings(request, user)


@router.post("/push/tokens", status_code=204)
async def register_push_token(body: PushTokenRequest, db: DbSession, user: AppUser) -> Response:
    """The app's FCM registration token (roadmap #6); a token moves to the current user."""
    await db.execute(
        pg_insert(PushToken)
        .values(user_id=user.id, token=body.token, platform=body.platform)
        .on_conflict_do_update(
            index_elements=["token"], set_={"user_id": user.id, "platform": body.platform}
        )
    )
    await db.commit()
    return Response(status_code=204)


@router.post("/push/tokens/delete", status_code=204)
async def delete_push_token(body: PushTokenRequest, db: DbSession, user: AppUser) -> Response:
    """On sign-out: this installation stops receiving notifications for this account."""
    await db.execute(
        delete(PushToken).where(PushToken.token == body.token, PushToken.user_id == user.id)
    )
    await db.commit()
    return Response(status_code=204)


@router.post("/account/password", status_code=204)
async def change_password(
    request: Request, body: PasswordChange, db: DbSession, user: AppUser
) -> Response:
    """Roadmap #10: signs out the web and every other app; this app stays signed in."""
    await _limit_account(request, user)
    try:
        await accounts.change_password(
            db, user, body.current, body.new, keep_app_token=hash_token(_bearer(request))
        )
    except accounts.AccountError as exc:
        await db.rollback()
        raise HTTPException(400, exc.message) from None
    await db.commit()
    return Response(status_code=204)


@router.post("/account/delete", status_code=204)
async def delete_account(
    request: Request, body: AccountDelete, db: DbSession, user: AppUser
) -> Response:
    """Roadmap #10: deletes the account at once, unpairs its cameras, removes their images."""
    await _limit_account(request, user)
    if not await verify_secret_async(user.password_hash, body.password):
        raise HTTPException(400, "Das Passwort stimmt nicht.")
    device_ids = await accounts.delete_account(db, user)
    await db.commit()
    await finish_deletion(request, device_ids)
    return Response(status_code=204)


@router.put("/cameras/{device_id}/public")
async def set_public(
    request: Request, body: PublicRequest, db: DbSession, user: AppUser, device_id: str
) -> Camera:
    """Opt-in public sky page (roadmap #9); switching off invalidates the link."""
    device = await _own(db, user, device_id)
    if body.enabled:
        public.enable(device)
    else:
        public.disable(device)
    await db.commit()
    return _camera(device, _registry(request), request.app.state.settings.base_url)
