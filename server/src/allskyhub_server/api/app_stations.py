"""App API for SharedSky stations (SPEC §10): "Kamera verbinden" and a station's state in
the app, the same as the web pages (devices/station_flow.py does the work for both).

Creating one is a multipart form like the web's, as an image address may come with a
proof picture. The secret is in the answer once and never again; renewing it and
removing a station ask for the password, like deleting the account.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from allskyhub_protocol import FrameVariant
from allskyhub_server.api.app import AppUser
from allskyhub_server.auth import ratelimit
from allskyhub_server.auth.passwords import verify_secret_async
from allskyhub_server.devices import image_url, public, station_flow, stations
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.i18n import tr
from allskyhub_server.models import Device, StationSource, User
from allskyhub_server.web.deps import DbSession, SettingsDep

router = APIRouter(prefix="/api/v1")

DOCS = "/entwickler/connect"


class Visibility(BaseModel):
    """SPEC §10.6 Visibility, the owner's four choices."""

    model_config = ConfigDict(extra="ignore")

    named: bool = True
    on_map: bool = True
    page: bool = True
    images: bool = False


class StationItem(BaseModel):
    id: str
    name: str
    software: str
    # connected, waiting (nothing received yet), pending, declined, paused (SPEC §10.7)
    state: str
    reason: str | None = None
    url: str | None = None
    last_seen_at: dt.datetime | None = None
    has_image: bool = False
    oriented: bool = False
    clock_trusted: bool = False
    frames_today: int = 0
    meteors_today: int = 0
    latitude: float | None = None
    longitude: float | None = None
    visibility: Visibility = Visibility()
    public_url: str | None = None
    # A station that joined before the choices: they hold from then on (§10.6); while its
    # owner's mail has not gone out, ``public_waiting`` without a date.
    public_from: dt.datetime | None = None
    public_waiting: bool = False


class StationCreated(BaseModel):
    station: StationItem
    # Once, for indi-allsky and other software; an image address has none.
    secret: str | None = None
    base_url: str | None = None  # indi-allsky's "Base URL"
    docs_url: str


class SecretAnswer(BaseModel):
    secret: str


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    password: str = Field(max_length=1024)


def _key(request: Request) -> SecretKey:
    return SecretKey(request.app.state.stores.vault)


async def _own(db: DbSession, user: User, device_id: str) -> Device:
    device = await db.get(Device, device_id)
    if device is None or device.kind != "station" or device.owner_id != user.id:
        raise HTTPException(404, "Not found")
    return device


async def _item(
    db: DbSession, device: Device, source: StationSource | None, base: str = ""
) -> StationItem:
    status: dict[str, Any] = device.last_status or {}
    place: dict[str, Any] = status.get("settings") or {}
    frames, meteors = await station_flow.today(db, device)
    if source is not None and source.state != "approved":
        state = source.state
    else:
        state = "connected" if device.last_seen_at is not None else "waiting"
    lat, lon = place.get("latitude"), place.get("longitude")
    return StationItem(
        id=device.id,
        name=device.name,
        software=device.software or "other",
        state=state,
        reason=source.reason if source is not None else None,
        url=source.url if source is not None else None,
        last_seen_at=device.last_seen_at,
        has_image=device.latest_thumb_at is not None,
        oriented=bool(status.get("orientation")),
        clock_trusted=bool((device.clock or {}).get("trusted")),
        frames_today=frames,
        meteors_today=meteors,
        latitude=float(lat) if isinstance(lat, int | float) else None,
        longitude=float(lon) if isinstance(lon, int | float) else None,
        visibility=Visibility(**vars(public.visibility(device))),
        public_url=f"{base.rstrip('/')}/sky/{device.public_slug}" if device.public_slug else None,
        public_from=device.public_from if device.public_from != public.UNMAILED else None,
        public_waiting=device.public_from is not None,
    )


async def _password(request: Request, user: User, password: str) -> None:
    try:
        await ratelimit.hit(
            request.app.state.engine, f"account:{user.id}", ratelimit.LOGIN_PER_ACCOUNT
        )
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Too many requests", {"Retry-After": str(exc.retry_after)}
        ) from None
    if not await verify_secret_async(user.password_hash, password):
        raise HTTPException(400, tr("Das Passwort stimmt nicht.", "The password is not right."))


@router.get("/stations")
async def list_stations(db: DbSession, settings: SettingsDep, user: AppUser) -> list[StationItem]:
    found = await db.scalars(
        select(Device)
        .where(Device.owner_id == user.id, Device.kind == "station")
        .order_by(Device.created_at)
    )
    out: list[StationItem] = []
    for device in found:
        source = await db.get(StationSource, device.id)
        out.append(await _item(db, device, source, settings.base_url))
    return out


@router.post("/stations")
async def create_station(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    user: AppUser,
    software: Annotated[Literal["indi-allsky", "image-url", "other"], Form()],
    name: Annotated[str, Form(max_length=100)],
    terms: Annotated[bool, Form()] = False,
    url: Annotated[str, Form(max_length=2048)] = "",
    latitude: Annotated[str, Form(max_length=20)] = "",
    longitude: Annotated[str, Form(max_length=20)] = "",
    elevation_m: Annotated[str, Form(max_length=10)] = "",
    timezone: Annotated[str, Form(max_length=64)] = "",
    focal_mm: Annotated[str, Form(max_length=10)] = "",
    proof_text: Annotated[str, Form(max_length=2000)] = "",
    proof: Annotated[UploadFile | None, File()] = None,
    named: Annotated[bool, Form()] = True,
    on_map: Annotated[bool, Form()] = True,
    page: Annotated[bool, Form()] = True,
    images: Annotated[bool, Form()] = False,
) -> StationCreated:
    """SPEC §10.1 and §10.7, like "Kamera verbinden" on the web; §10.6 Visibility with its
    defaults when the app does not ask (1.2.0)."""
    proof_bytes = b""
    if software == "image-url" and proof is not None and proof.filename:
        proof_bytes = await proof.read(image_url.PROOF_MAX_UPLOAD + 1)
    ask = station_flow.Ask(
        software=software, name=name, terms=terms, url=url, latitude=latitude,
        longitude=longitude, elevation_m=elevation_m, timezone=timezone, focal_mm=focal_mm,
        proof_text=proof_text, proof=proof_bytes,
        visibility=public.Visibility(named=named, on_map=on_map, page=page, images=images),
    )  # fmt: skip
    try:
        device, secret = await station_flow.create(
            db, user, ask, _key(request), settings, request.app.state.mailer
        )
    except station_flow.StationInputError as exc:
        raise HTTPException(400, exc.message) from None
    base = settings.base_url.rstrip("/")
    return StationCreated(
        station=await _item(db, device, await db.get(StationSource, device.id), base),
        secret=secret,
        base_url=f"{base}/indi-allsky" if software == "indi-allsky" else None,
        docs_url=f"{base}{DOCS}",
    )


@router.get("/stations/{device_id}")
async def get_station(
    db: DbSession, settings: SettingsDep, user: AppUser, device_id: str
) -> StationItem:
    device = await _own(db, user, device_id)
    return await _item(db, device, await db.get(StationSource, device.id), settings.base_url)


@router.put("/stations/{device_id}/visibility")
async def set_visibility(
    db: DbSession, settings: SettingsDep, user: AppUser, device_id: str, body: Visibility
) -> StationItem:
    """SPEC §10.6 Visibility; switching the page off ends its address."""
    device = await _own(db, user, device_id)
    public.set_visibility(device, public.Visibility(**body.model_dump()))
    await db.commit()
    return await _item(db, device, await db.get(StationSource, device.id), settings.base_url)


@router.get("/stations/{device_id}/image.jpg")
async def station_image(request: Request, db: DbSession, user: AppUser, device_id: str) -> Response:
    device = await _own(db, user, device_id)
    store: ImageStore = request.app.state.images
    path = store.path(device.id, FrameVariant.THUMB)
    if not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/stations/{device_id}/secret")
async def renew_secret(
    request: Request, db: DbSession, user: AppUser, device_id: str, body: PasswordBody
) -> SecretAnswer:
    device = await _own(db, user, device_id)
    if device.software == "image-url":
        raise HTTPException(404, "Not found")
    await _password(request, user, body.password)
    secret = stations.rotate_secret(device, _key(request))
    await db.commit()
    return SecretAnswer(secret=secret)


@router.post("/stations/{device_id}/delete", status_code=204)
async def delete_station(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    user: AppUser,
    device_id: str,
    body: PasswordBody,
) -> Response:
    """SPEC §10.1: images, the exact place and the secret go at once; measurements stay."""
    device = await _own(db, user, device_id)
    await _password(request, user, body.password)
    await station_flow.remove(db, device, request.app.state, settings)
    return Response(status_code=204)
