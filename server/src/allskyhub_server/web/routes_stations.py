"""SharedSky pages (SPEC §10): "Kamera verbinden", a station's page, the admin's check of
stations by their image address (§10.7), and the public /mitmachen and /entwickler/connect.

A station has its own pages under /stationen; /cameras/<station> leads there (a station
has no camera settings, live view or commands). Renewing the secret and removing a
station ask for the owner's password again, approving and declining one the admin's.
"""

from __future__ import annotations

import base64
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_protocol import FrameVariant
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.devices import image_url, map_tiles, pairing, stations
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.models import Device, EventRecord, Frame, StationSource, User
from allskyhub_server.settings import Settings
from allskyhub_server.web import flash
from allskyhub_server.web.deps import (
    CurrentSession,
    DbSession,
    OptionalSession,
    SettingsDep,
    csrf_protect,
)
from allskyhub_server.web.render import render
from allskyhub_server.web.routes_admin import admin_only, confirmed

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(csrf_protect)])

SOFTWARE = {
    "indi-allsky": "indi-allsky",
    "image-url": "Allsky / Bild-Adresse",
    "other": "andere oder eigene Software",
}
STATES = {
    "pending": "wartet auf Prüfung",
    "approved": "freigegeben",
    "declined": "abgelehnt",
    "paused": "pausiert",
}
DOCS = "/entwickler/connect"


def _secret_key(request: Request) -> SecretKey:
    return SecretKey(request.app.state.stores.vault)


def _proofs(settings: Settings) -> Any:
    return settings.data_dir / "station-proofs"


def _masks(settings: Settings) -> Any:
    return settings.data_dir / "station-masks"


async def _own_station(db: AsyncSession, session: SessionInfo, device_id: str) -> Device:
    device = await db.get(Device, device_id)
    if device is None or device.kind != "station" or device.owner_id != session.user.id:
        raise HTTPException(404)
    return device


def _map_config(lat: float | None, lon: float | None, editable: bool) -> dict[str, Any]:
    return {
        "maxZoom": map_tiles.MAX_ZOOM,
        "layers": {
            key: {"maxZoom": t.max_zoom, "credit": t.credit} for key, t in map_tiles.LAYERS.items()
        },
        "point": [lat, lon] if lat is not None and lon is not None else None,
        "editable": editable,
    }


# "Kamera verbinden"


@router.get("/stationen/neu")
async def new_form(request: Request, session: CurrentSession, software: str = "") -> Response:
    return render(
        request,
        "station_new.html",
        {
            "software": software if software in SOFTWARE else "",
            "choices": SOFTWARE,
            "form": {},
            "map_config": _map_config(None, None, editable=True),
        },
        session=session,
    )


@dataclass(frozen=True)
class _Place:
    lat: float
    lon: float
    elevation_m: float | None


def _place(lat: str, lon: str, elevation: str) -> _Place:
    try:
        la, lo = float(lat.replace(",", ".")), float(lon.replace(",", "."))
        el = float(elevation.replace(",", ".")) if elevation.strip() else None
    except ValueError:
        raise ValueError(
            "Bitte den Ort auf der Karte setzen oder die Koordinaten eingeben."
        ) from None
    if not (-90 <= la <= 90 and -180 <= lo <= 180) or (el is not None and not -500 <= el <= 9000):
        raise ValueError("Die Koordinaten liegen außerhalb der Erde.")
    return _Place(la, lo, el)


@router.post("/stationen/neu")
async def create(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    session: CurrentSession,
    software: Annotated[str, Form(max_length=20)],
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
) -> Response:
    """SPEC §10.1 creating a station; §10.7 asking with an image address."""
    form = {
        "name": name, "url": url, "latitude": latitude, "longitude": longitude,
        "elevation_m": elevation_m, "timezone": timezone, "focal_mm": focal_mm,
        "proof_text": proof_text,
    }  # fmt: skip

    def again(error: str) -> Response:
        return render(
            request,
            "station_new.html",
            {"software": software, "choices": SOFTWARE, "form": form, "error": error,
             "map_config": _map_config(None, None, editable=True)},
            session=session,
            status_code=400,
        )  # fmt: skip

    if software not in SOFTWARE:
        return again("Bitte die Software deiner Kamera wählen.")
    if not name.strip():
        return again("Bitte einen Namen für die Station angeben.")
    if not terms:
        return again("Bitte den Teilnahmebedingungen zustimmen.")
    place: _Place | None = None
    proof_bytes = b""
    if software == "image-url":
        try:
            image_url.check_url(url)
        except image_url.FetchError as exc:
            return again(exc.text)
        try:
            place = _place(latitude, longitude, elevation_m)
        except ValueError as exc:
            return again(str(exc))
        if proof is not None and proof.filename:
            proof_bytes = await proof.read(image_url.PROOF_MAX_UPLOAD + 1)
        if not proof_text.strip() and not proof_bytes:
            return again("Bitte einen Nachweis angeben: einen Satz, einen Link oder ein Bild.")
    user = await db.get(User, session.user.id)
    if user is None:
        raise HTTPException(404)
    device, secret = await stations.create(db, user, name, software, _secret_key(request))
    if software == "image-url" and place is not None:
        if proof_bytes:
            try:
                image_url.store_proof(_proofs(settings), device.id, proof_bytes)
            except image_url.FetchError as exc:
                await db.rollback()
                return again(f"Das Nachweis-Bild: {exc.text}")
        lens = {"focal_mm": float(focal_mm.replace(",", "."))} if _number(focal_mm) else None
        device.last_status = {
            "settings": {"latitude": place.lat, "longitude": place.lon,
                         "elevation_m": place.elevation_m, "timezone": timezone.strip() or None},
            "station": {"software": "image-url", "lens": lens},
        }  # fmt: skip
        db.add(StationSource(
            device_id=device.id, url=url.strip(), proof_text=proof_text.strip(),
            proof_image=bool(proof_bytes),
        ))  # fmt: skip
        await db.commit()
        log.info("station asked by image address", extra={"device_id": device.id})
        await _tell_admins(request, settings, device, user)
        return RedirectResponse(f"/stationen/{device.id}", status_code=303)
    await db.commit()
    log.info("station created", extra={"device_id": device.id, "software": software})
    return flash.redirect(request, session, f"/stationen/{device.id}", 200, {"secret": secret})


def _number(text: str) -> bool:
    try:
        return float(text.replace(",", ".")) > 0
    except ValueError:
        return False


async def _tell_admins(request: Request, settings: Settings, device: Device, user: User) -> None:
    mailer: Mailer = request.app.state.mailer
    if not mailer.enabled:
        return
    link = f"{settings.base_url.rstrip('/')}/admin/stationen/{device.id}"
    for admin in settings.admins:
        await mailer.send(Mail(
            to=admin, subject=f"SharedSky: {device.name} wartet auf Prüfung",
            body=f"Hallo,\n\n{user.email} möchte eine Kamera über ihre Bild-Adresse "
                 f"verbinden:\n\n{link}\n\nallskyhub\n",
        ))  # fmt: skip


# A station's page


async def _today(db: AsyncSession, device: Device) -> tuple[int, int]:
    night = stations.night_id(dt.datetime.now(dt.UTC), stations.longitude(device))
    frames = await db.scalar(
        select(func.count())
        .select_from(Frame)
        .where(Frame.device_id == device.id, Frame.night_id == night)
    )
    meteors = await db.scalar(
        select(func.count())
        .select_from(EventRecord)
        .where(
            EventRecord.device_id == device.id,
            EventRecord.night_id == night,
            EventRecord.kind == "meteor",
        )
    )
    return int(frames or 0), int(meteors or 0)


@router.get("/stationen/{device_id}")
async def station_page(
    request: Request, db: DbSession, settings: SettingsDep, session: CurrentSession, device_id: str
) -> Response:
    device = await _own_station(db, session, device_id)
    status, kept = flash.take(request, session)
    source = await db.get(StationSource, device.id)
    frames, meteors = await _today(db, device)
    orientation: dict[str, Any] | None = (device.last_status or {}).get("orientation")
    settings_: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    return render(
        request,
        "station.html",
        {
            "device": device,
            "source": source,
            "states": STATES,
            "software": SOFTWARE.get(device.software or "", device.software),
            "base": settings.base_url.rstrip("/"),
            "frames": frames,
            "meteors": meteors,
            "orientation": orientation,
            "clock_trusted": bool((device.clock or {}).get("trusted")),
            "time_source": (device.last_frame or {}).get("time_source"),
            "place": settings_,
            "docs": DOCS,
            **kept,
        },
        session=session,
        status_code=status,
    )


@router.get("/stationen/{device_id}/bild.jpg")
async def station_image(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    device = await _own_station(db, session, device_id)
    store: ImageStore = request.app.state.images
    path = store.path(device.id, FrameVariant.THUMB)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/stationen/{device_id}/geheimnis")
async def new_secret(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    device = await _own_station(db, session, device_id)
    back = f"/stationen/{device.id}"
    if device.software == "image-url":
        raise HTTPException(404)
    if not await confirmed(request, session, password):
        return flash.redirect(request, session, back, 403, {"error": "Dein Passwort stimmt nicht."})
    secret = stations.rotate_secret(device, _secret_key(request))
    await db.commit()
    log.info("station secret renewed", extra={"device_id": device.id})
    return flash.redirect(request, session, back, 200, {"secret": secret})


@router.post("/stationen/{device_id}/fortsetzen")
async def resume(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """A station paused after a day of errors goes on (one an admin paused does not)."""
    device = await _own_station(db, session, device_id)
    source = await db.get(StationSource, device.id)
    if source is None or source.state != "paused" or source.reviewed_by == "pause":
        raise HTTPException(404)
    source.state, source.errors, source.errors_since = "approved", 0, None
    source.next_fetch_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return flash.redirect(
        request,
        session,
        f"/stationen/{device.id}",
        200,
        {"notice": "Die Station holt wieder Bilder."},
    )


@router.post("/stationen/{device_id}/entfernen")
async def remove(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    session: CurrentSession,
    device_id: str,
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    """SPEC §10.1: images, the exact place and the secret go at once; measurements stay."""
    device = await _own_station(db, session, device_id)
    if not await confirmed(request, session, password):
        return flash.redirect(
            request,
            session,
            f"/stationen/{device.id}",
            403,
            {"error": "Dein Passwort stimmt nicht."},
        )
    source = await db.get(StationSource, device.id)
    if source is not None:
        await db.delete(source)
    await pairing.unpair(db, device)
    await db.commit()
    store: ImageStore = request.app.state.images
    store.delete_device(device.id)
    image_url.proof_path(_proofs(settings), device.id).unlink(missing_ok=True)
    image_url.OverlayMask(_masks(settings)).forget(device.id)
    request.app.state.station_detector.buffer.drop(device.id)
    log.info("station removed", extra={"device_id": device.id})
    return RedirectResponse("/", status_code=303)


# The admin's check (SPEC §10.7)


@router.get("/admin/stationen")
async def admin_list(
    request: Request, db: DbSession, settings: SettingsDep, session: CurrentSession
) -> Response:
    admin_only(session, settings)
    status, kept = flash.take(request, session)
    rows = (
        await db.execute(
            select(StationSource, Device, User)
            .join(Device, Device.id == StationSource.device_id)
            .outerjoin(User, User.id == Device.owner_id)
            .order_by((StationSource.state != "pending"), StationSource.requested_at.desc())
            .limit(500)
        )
    ).all()
    return render(
        request,
        "admin_stations.html",
        {"rows": rows, "states": STATES, **kept},
        session=session,
        status_code=status,
    )


async def _admin_station(
    db: AsyncSession, device_id: str
) -> tuple[StationSource, Device, User | None]:
    source = await db.get(StationSource, device_id)
    device = await db.get(Device, device_id)
    if source is None or device is None:
        raise HTTPException(404)
    owner = await db.get(User, device.owner_id) if device.owner_id else None
    return source, device, owner


@router.get("/admin/stationen/{device_id}")
async def admin_station(
    request: Request, db: DbSession, settings: SettingsDep, session: CurrentSession, device_id: str
) -> Response:
    """Fetched once on opening, through the same safe fetcher as the stations."""
    admin_only(session, settings)
    source, device, owner = await _admin_station(db, device_id)
    status, kept = flash.take(request, session)
    fetcher: image_url.Fetcher = request.app.state.image_fetcher
    thumb, problem = await image_url.preview(fetcher, source.url)
    place: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = place.get("latitude"), place.get("longitude")
    return render(
        request,
        "admin_station.html",
        {
            "source": source,
            "device": device,
            "owner": owner,
            "states": STATES,
            "place": place,
            "preview": base64.b64encode(thumb).decode() if thumb else None,
            "problem": problem,
            "map_config": _map_config(
                float(lat) if isinstance(lat, int | float) else None,
                float(lon) if isinstance(lon, int | float) else None,
                editable=False,
            ),
            **kept,
        },
        session=session,
        status_code=status,
    )


@router.get("/admin/stationen/{device_id}/nachweis.jpg")
async def admin_proof(
    db: DbSession, settings: SettingsDep, session: CurrentSession, device_id: str
) -> Response:
    admin_only(session, settings)
    source, _, _ = await _admin_station(db, device_id)
    path = image_url.proof_path(_proofs(settings), device_id)
    if not source.proof_image or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


DECISIONS = {
    "freigeben": ("approved", "freigegeben"),
    "ablehnen": ("declined", "abgelehnt"),
    "pausieren": ("paused", "pausiert"),
}


@router.post("/admin/stationen/{device_id}/{decision}")
async def admin_decide(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    session: CurrentSession,
    device_id: str,
    decision: str,
    password: Annotated[str, Form(max_length=1024)],
    reason: Annotated[str, Form(max_length=500)] = "",
) -> Response:
    admin_only(session, settings)
    if decision not in DECISIONS:
        raise HTTPException(404)
    source, device, owner = await _admin_station(db, device_id)
    back = f"/admin/stationen/{device_id}"
    if not await confirmed(request, session, password):
        return flash.redirect(request, session, back, 403, {"error": "Dein Passwort stimmt nicht."})
    state, word = DECISIONS[decision]
    if state == "declined" and not reason.strip():
        return flash.redirect(request, session, back, 400, {"error": "Bitte einen Grund angeben."})
    now = dt.datetime.now(dt.UTC)
    source.state, source.reason = state, reason.strip()
    source.reviewed_at = now
    # "pause": an admin paused it (a removal ask), so the owner cannot resume it.
    source.reviewed_by = "pause" if state == "paused" else session.user.email
    if state == "approved":
        source.next_fetch_at, source.errors, source.errors_since = now, 0, None
    await db.commit()
    log.info(
        "admin station decision",
        extra={"admin": session.user.email, "action": decision, "device_id": device_id},
    )
    mailer: Mailer = request.app.state.mailer
    if owner is not None and mailer.enabled:
        page = f"{settings.base_url.rstrip('/')}/stationen/{device.id}"
        why = f"\nBegründung: {source.reason}\n" if source.reason else ""
        text = {
            "approved": "ist freigegeben. Der Hub holt ab jetzt ihr Bild und misst mit.",
            "declined": "wurde nicht freigegeben.",
            "paused": "ist vorerst pausiert; der Hub holt kein Bild mehr ab.",
        }[state]
        await mailer.send(Mail(
            to=owner.email, subject=f"SharedSky: {device.name} {word}",
            body=f"Hallo,\n\ndeine Station „{device.name}“ {text}\n{why}\n{page}\n\nallskyhub\n",
        ))  # fmt: skip
    return flash.redirect(request, session, back, 200, {"notice": f"Station {word}."})


# Public


@router.get("/mitmachen")
async def join(request: Request, session: OptionalSession) -> Response:
    """SharedSky explained, for the announcement (``open_signup`` comes from render)."""
    return render(request, "mitmachen.html", {}, session=session)


@router.get("/entwickler/connect")
async def developers(request: Request, session: OptionalSession, settings: SettingsDep) -> Response:
    return render(
        request,
        "entwickler_connect.html",
        {"base": settings.base_url.rstrip("/")},
        session=session,
    )
