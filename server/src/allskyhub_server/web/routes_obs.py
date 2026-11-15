"""The stream view (draft C, Benjamin 2026-11-15): a camera's latest image with chosen
layers and a line of text, full screen and without the page around it, for OBS's browser
source and other stream tools.

The owner makes a key on ``/cameras/<id>/stream`` (one per camera; a new one replaces it,
and it can be revoked). ``/obs/<key>`` works without signing in, also for a camera that is
not public, and only while that user still owns the camera. The place is never written.
Satellites and balloons give the exact place away (web/templating.sky_layers): a key
shows them only when the owner allowed them when making it (``ObsToken.place_layers``);
the address can only leave layers out. The key is in the path: the hub's log
(app.log_path) and nginx (deploy/nginx) write it masked.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from allskyhub_protocol import FrameVariant
from allskyhub_server.auth.tokens import hash_token, new_token
from allskyhub_server.devices import queries
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, ObsToken, User
from allskyhub_server.web import flash
from allskyhub_server.web.deps import CurrentSession, DbSession, csrf_protect
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])

# ?ebenen=… : the address's word → the class on <html> that shows the layer (overlay.js).
LAYERS = {
    "sternbilder": "sky-on",
    "namen": "stars-on",
    "raster": "grid-on",
    "planeten": "planets-on",
    "satelliten": "sats-on",
    "ballons": "balloons-on",
}
TELLS_PLACE = ("satelliten", "ballons")
DEFAULT_LAYERS = ("sternbilder",)
REFRESH_S = 20
# Like the widget (routes_public.EMBED_CSP): any site and OBS may show it, nothing loads
# from elsewhere.
OBS_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors *; base-uri 'self'; form-action 'none'"
)


async def _own(db: DbSession, user_id: Any, device_id: str) -> Device:
    device = await queries.owned_one(db, user_id, device_id)
    if device is None or device.kind == "station":
        raise HTTPException(404)
    return device


# The owner's page


@router.get("/cameras/{device_id}/stream")
async def stream_settings(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    device = await _own(db, session.user.id, device_id)
    status, kept = flash.take(request, session)
    key = await db.scalar(select(ObsToken).where(ObsToken.device_id == device.id))
    return render(
        request,
        "obs_settings.html",
        {
            "device": device,
            "obs_key": key,
            "token": kept.get("token"),
            "layers": LAYERS,
            "tells_place": TELLS_PLACE,
            "defaults": DEFAULT_LAYERS,
            "base": str(request.base_url).rstrip("/"),
        },
        session=session,
        status_code=status,
    )


@router.post("/cameras/{device_id}/stream")
async def stream_new_key(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    place_layers: Annotated[bool, Form()] = False,
) -> Response:
    """A new key; the old address stops working at once. ``place_layers``: satellites and
    balloons may be shown (they give the place away)."""
    device = await _own(db, session.user.id, device_id)
    token = new_token()
    values = {"token_hash": hash_token(token), "user_id": session.user.id,
              "place_layers": place_layers}  # fmt: skip
    await db.execute(
        pg_insert(ObsToken)
        .values(device_id=device.id, **values)
        .on_conflict_do_update(
            constraint="uq_obs_token_device_id", set_={**values, "created_at": func.now()}
        )
    )  # one per camera, also when the button is pressed twice
    await db.commit()
    return flash.redirect(request, session, f"/cameras/{device.id}/stream", 200, {"token": token})


@router.post("/cameras/{device_id}/stream/loeschen")
async def stream_revoke(db: DbSession, session: CurrentSession, device_id: str) -> RedirectResponse:
    device = await _own(db, session.user.id, device_id)
    await db.execute(delete(ObsToken).where(ObsToken.device_id == device.id))
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}/stream", status_code=303)


# The view


async def _device(db: DbSession, token: str) -> tuple[Device, ObsToken]:
    row = (
        await db.execute(
            select(ObsToken, User)
            .join(User, User.id == ObsToken.user_id)
            .where(ObsToken.token_hash == hash_token(token))
        )
    ).one_or_none()
    if row is None or not row[1].is_active:
        raise HTTPException(404)
    key, user = row
    device = await queries.owned_one(db, user.id, key.device_id)
    if device is None:
        raise HTTPException(404)
    return device, key


def _chosen(request: Request, key: ObsToken) -> list[str]:
    """The layers of the address, without those the key does not allow: the address can
    only leave layers out."""
    raw = request.query_params.get("ebenen")
    words = list(DEFAULT_LAYERS) if raw is None else raw.split(",")
    return [w for w in words if w in LAYERS and (key.place_layers or w not in TELLS_PLACE)]


def _context(request: Request, device: Device, key: ObsToken, token: str) -> dict[str, Any]:
    chosen = _chosen(request, key)
    query = request.url.query
    return {
        "device": device,
        "token": token,
        "classes": " ".join(LAYERS[w] for w in chosen),
        "satellites": any(w in TELLS_PLACE for w in chosen),
        "text": request.query_params.get("text", "unten") != "aus",
        "red": request.query_params.get("rotlicht") == "1",
        "query": f"?{query}" if query else "",
        "refresh_s": REFRESH_S,
    }


def _framed(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = OBS_CSP
    response.headers["X-Frame-Options"] = "ALLOWALL"  # ignored by browsers; CSP decides
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@router.get("/obs/{token}")
async def obs_view(request: Request, db: DbSession, token: str) -> Response:
    device, key = await _device(db, token)
    return _framed(render(request, "obs.html", _context(request, device, key, token)))


@router.get("/obs/{token}/rahmen")
async def obs_frame(request: Request, db: DbSession, token: str) -> Response:
    """The picture and its layers, fetched by obs.js when a new image may have come."""
    device, key = await _device(db, token)
    return _framed(render(request, "_obs_frame.html", _context(request, device, key, token)))


@router.get("/obs/{token}/bild.jpg")
async def obs_image(request: Request, db: DbSession, token: str) -> Response:
    device, _ = await _device(db, token)
    store: ImageStore = request.app.state.images
    path = store.path(device.id, FrameVariant.FULL)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})
