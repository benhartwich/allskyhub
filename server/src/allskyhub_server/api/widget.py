"""Home-screen widgets (roadmap N6): a read-only key per camera and what a widget shows.

The app asks for a key (``POST /api/v1/cameras/{id}/widget``) and hands it to the widget,
which keeps it outside the app's secure storage. With it the widget reads the latest
picture and a few lines, nothing else, and only while its user may still see the camera.
A new key for the same camera replaces the old one; removing the widget deletes it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, select

from allskyhub_protocol import FrameVariant
from allskyhub_server.api.app import AppUser
from allskyhub_server.auth.tokens import hash_token, new_token
from allskyhub_server.devices import queries, tonight
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, User, WidgetToken
from allskyhub_server.web.deps import DbSession

router = APIRouter(prefix="/api/v1")


class WidgetKey(BaseModel):
    token: str


class WidgetState(BaseModel):
    name: str
    online: bool
    image_at: dt.datetime | None
    # "Heute Nacht" in one sentence, or None without a place or forecast.
    tonight: str | None
    # Cloud cover now (0..1) from the camera's own measurement, None by day.
    cloud_cover: float | None


async def _visible(db: DbSession, user: User, device_id: str) -> Device:
    found = await queries.visible_one(db, user.id, device_id)
    if found is None:
        raise HTTPException(404)
    return found[0]


@router.post("/cameras/{device_id}/widget")
async def widget_key(db: DbSession, user: AppUser, device_id: str) -> WidgetKey:
    device = await _visible(db, user, device_id)
    await db.execute(
        delete(WidgetToken).where(
            WidgetToken.user_id == user.id, WidgetToken.device_id == device.id
        )
    )
    token = new_token()
    db.add(WidgetToken(token_hash=hash_token(token), user_id=user.id, device_id=device.id))
    await db.commit()
    return WidgetKey(token=token)


@router.delete("/cameras/{device_id}/widget", status_code=204)
async def widget_key_delete(db: DbSession, user: AppUser, device_id: str) -> Response:
    await db.execute(
        delete(WidgetToken).where(
            WidgetToken.user_id == user.id, WidgetToken.device_id == device_id
        )
    )
    await db.commit()
    return Response(status_code=204)


async def _widget_device(request: Request, db: DbSession) -> Device:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise HTTPException(401, "Missing widget key", {"WWW-Authenticate": "Bearer"})
    row = (
        await db.execute(
            select(WidgetToken, User)
            .join(User, User.id == WidgetToken.user_id)
            .where(WidgetToken.token_hash == hash_token(value.strip()))
        )
    ).one_or_none()
    if row is None or not row[1].is_active:
        raise HTTPException(401, "Invalid widget key", {"WWW-Authenticate": "Bearer"})
    key, user = row
    # Sharing may have ended, or the camera been removed since.
    found = await queries.visible_one(db, user.id, key.device_id)
    if found is None:
        raise HTTPException(401, "Invalid widget key", {"WWW-Authenticate": "Bearer"})
    return found[0]


@router.get("/widget")
async def widget_state(request: Request, db: DbSession) -> WidgetState:
    device = await _widget_device(request, db)
    registry: ConnectionRegistry = request.app.state.connections
    now = dt.datetime.now(dt.UTC)
    night = await tonight.for_device(request.app.state.forecast, device.last_status, now)
    sky: dict[str, Any] = (device.last_status or {}).get("sky") or {}
    cover = sky.get("cloud_cover")
    return WidgetState(
        name=device.name or "Allsky-Kamera",
        online=registry.is_online(device.id),
        image_at=device.latest_image_at,
        tonight=night.summary if night is not None else None,
        cloud_cover=float(cover) if isinstance(cover, int | float) else None,
    )


def _latest(store: ImageStore, device_id: str) -> Path | None:
    for variant in (FrameVariant.THUMB, FrameVariant.FULL):
        path = store.path(device_id, variant)
        if path.is_file():
            return path
    return None


@router.get("/widget/image.jpg")
async def widget_image(request: Request, db: DbSession) -> Response:
    device = await _widget_device(request, db)
    path = _latest(request.app.state.images, device.id)
    if path is None:
        raise HTTPException(404)
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=60"}
    )
