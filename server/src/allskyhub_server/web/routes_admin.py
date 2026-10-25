"""Admin page: the server's state, all cameras and deploying new versions (admin/deploy.py).

Only for accounts in ``admin_emails``; everyone else gets a 404, as if the page did not
exist. Starting a deploy needs the password again.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from starlette.routing import Match

from allskyhub_server.admin import deploy
from allskyhub_server.admin import stores as admin_stores
from allskyhub_server.auth import access_requests, ratelimit
from allskyhub_server.auth.accounts import AccountError
from allskyhub_server.auth.passwords import verify_secret_async
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.devices import youtube
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.models import Device, User
from allskyhub_server.settings import Settings
from allskyhub_server.version import full as running_version
from allskyhub_server.web.deps import CurrentSession, DbSession, SettingsDep, csrf_protect
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])

UPDATE_STATES = {
    "downloading": "lädt ein Update",
    "waiting": "Update wartet auf den Tag",
    "installing": "installiert ein Update",
    "installed": "Update installiert",
    "rolled_back": "Update zurückgerollt",
    "failed": "Update fehlgeschlagen",
}


def admin_only(session: SessionInfo, settings: Settings) -> None:
    if session.user.email.lower() not in settings.admins:
        raise HTTPException(404)


async def confirmed(request: Request, session: SessionInfo, password: str) -> bool:
    """The admin's password again, for deploys and store keys (rate limited like logins)."""
    try:
        await ratelimit.hit(
            request.app.state.engine, f"account:{session.user.id}", ratelimit.LOGIN_PER_ACCOUNT
        )
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Zu viele Versuche.", {"Retry-After": str(exc.retry_after)}
        ) from None
    return await verify_secret_async(session.user.password_hash, password)


@dataclass(frozen=True)
class CameraRow:
    device: Device
    email: str
    online: bool
    update: str | None  # the updater's state unless it is up to date (SPEC §8)


async def all_cameras(db: DbSession, registry: ConnectionRegistry) -> list[CameraRow]:
    """Every paired camera without its pictures, for help and for following updates
    (Benjamin, 2026-10-24): online first, then by when it was last seen."""
    rows = (
        await db.execute(
            select(Device, User.email)
            .join(User, User.id == Device.owner_id)
            .order_by(Device.last_seen_at.desc().nulls_last(), Device.name)
        )
    ).all()
    cameras: list[CameraRow] = []
    for device, email in rows:
        update = cast(dict[str, Any], (device.last_status or {}).get("update") or {})
        state = update.get("state")
        cameras.append(
            CameraRow(
                device,
                email,
                registry.is_online(device.id),
                state if isinstance(state, str) and state != "up_to_date" else None,
            )
        )
    cameras.sort(key=lambda c: not c.online)  # stable: keeps last seen within each part
    return cameras


async def page(
    request: Request,
    db: DbSession,
    session: SessionInfo,
    status_code: int = 200,
    **extra: object,
) -> Response:
    settings: Settings = request.app.state.settings
    github: deploy.GitHub = request.app.state.github
    stores: admin_stores.Stores = request.app.state.stores
    running = running_version()
    upstream: deploy.Upstream | None = None
    upstream_error: str | None = None
    try:
        upstream = await github.upstream(running)
    except deploy.DeployError as exc:
        upstream_error = str(exc)
    disk = await asyncio.to_thread(shutil.disk_usage, settings.data_dir)
    return render(
        request,
        "admin.html",
        {
            "running": running,
            "upstream": upstream,
            "upstream_error": upstream_error,
            "job": deploy.job(settings.data_dir),
            "disk_free_gb": disk.free / 1e9,
            "disk_free_pct": 100 * disk.free / disk.total,
            "users": await db.scalar(select(func.count()).select_from(User)),
            "cameras": await db.scalar(
                select(func.count()).select_from(Device).where(Device.owner_id.is_not(None))
            ),
            "camera_rows": await all_cameras(db, request.app.state.connections),
            "update_states": UPDATE_STATES,
            "repo": settings.deploy_repo,
            "access_requests": await access_requests.open_requests(db),
            "stages": access_requests.STAGES,
            "plus": (
                await db.execute(
                    select(Device, User.email)
                    .join(User, User.id == Device.owner_id)
                    .where(Device.plus_gift.is_(True) | Device.plus_until.is_not(None))
                    .order_by(User.email, Device.name)
                )
            ).all(),
            "stores": stores.vault.entries(),
            "store_titles": admin_stores.TITLES,
            "youtube_redirect": youtube.redirect_uri(request.app.state.settings.base_url),
            "store_builds": await stores.recent_builds(),
            "codemagic_app": (stores.vault.meta("codemagic") or {}).get("app_id"),
            "android_job": stores.android_job(),
            "android_states": {
                "requested": "angefordert",
                "running": "baut",
                "ok": "bei Google Play",
                "built": "gebaut, erstes Hochladen von Hand",
                "failed": "fehlgeschlagen",
                "idle": "fertig",
            },
            "android_key": stores.vault.has("android_upload"),
            "screenshots": len(stores.screenshots()),
            "listing": stores.listing_state(),
            "uploaded_screenshots": stores.screenshot_dir.is_dir()
            and any(stores.screenshot_dir.iterdir()),
            "build_url": admin_stores.build_url,
            "package": admin_stores.PACKAGE,
            **extra,
        },
        session=session,
        status_code=status_code,
    )


@router.get("/admin")
async def admin_page(
    request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep
) -> Response:
    admin_only(session, settings)
    return await page(request, db, session)


@router.get("/admin/deploy/job")
async def deploy_job(request: Request, session: CurrentSession, settings: SettingsDep) -> Response:
    """The helper's progress, polled by the page while a deploy runs."""
    admin_only(session, settings)
    return render(
        request,
        "_deploy_job.html",
        {"job": deploy.job(settings.data_dir), "running": running_version()},
        session=session,
    )


@router.post("/admin/deploy")
async def start_deploy(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    sha: Annotated[str, Form(max_length=40)],
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    admin_only(session, settings)
    if not await confirmed(request, session, password):
        return await page(request, db, session, 400, error="Das Passwort stimmt nicht.")
    github: deploy.GitHub = request.app.state.github
    github.forget()  # decide on GitHub's current answer, not a cached one
    try:
        upstream = await github.upstream(running_version())
        deploy.request(settings.data_dir, sha, session.user.email, running_version(), upstream)
    except deploy.DeployError as exc:
        return await page(request, db, session, 400, error=str(exc))
    return await page(
        request, db, session, notice="Deploy angefordert. Der Server holt ihn gleich ab."
    )


@router.post("/admin/plus")
async def gift_plus(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    device_id: Annotated[str, Form(max_length=26)],
    gift: Annotated[bool, Form()] = False,
) -> Response:
    """Roadmap N7: Plus as a gift (it never ends), or taking the gift back."""
    admin_only(session, settings)
    device = await db.get(Device, device_id.strip().lower())
    if device is None or device.owner_id is None:
        return await page(request, db, session, 400, error="Keine gekoppelte Kamera mit dieser ID.")
    device.plus_gift = gift
    await db.commit()
    name = device.name or device.id
    notice = f"Plus für „{name}“ geschenkt." if gift else f"Plus-Geschenk für „{name}“ beendet."
    return await page(request, db, session, notice=notice)


@router.post("/admin/mail-test")
async def mail_test(
    request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep
) -> Response:
    admin_only(session, settings)
    mailer: Mailer = request.app.state.mailer
    if not mailer.enabled:
        return await page(request, db, session, 400, error="E-Mail ist nicht eingerichtet.")
    sent = await mailer.send(
        Mail(
            to=session.user.email,
            subject="allskyhub: Testmail",
            body="Diese Testmail kam von der Admin-Seite. Der Versand funktioniert.\n",
        )
    )
    if not sent:
        return await page(
            request, db, session, 502, error="Der Mailserver hat die Mail nicht angenommen."
        )
    return await page(request, db, session, notice=f"Testmail an {session.user.email} ist raus.")


@router.post("/admin/access/{request_id}/invite")
async def invite_request(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    request_id: uuid.UUID,
) -> Response:
    """Invite whoever asked on /zugang: the invitation link goes out by mail."""
    admin_only(session, settings)
    try:
        asked, link, mail = await access_requests.invite(db, request_id, settings.base_url)
    except AccountError as exc:
        await db.rollback()
        return await page(request, db, session, 400, error=exc.message)
    await db.commit()
    mailer: Mailer = request.app.state.mailer
    if mailer.enabled and await mailer.send(mail):
        return await page(request, db, session, notice=f"Einladung an {asked.email} ist raus.")
    # No mail: the admin passes the link on by hand.
    return await page(
        request, db, session,
        notice=f"Die Mail an {asked.email} ging nicht raus. Schick den Link selbst: {link}",
    )  # fmt: skip


@router.post("/admin/access/{request_id}/decline")
async def decline_request(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    request_id: uuid.UUID,
) -> Response:
    admin_only(session, settings)
    try:
        asked = await access_requests.decline(db, request_id)
    except AccountError as exc:
        return await page(request, db, session, 400, error=exc.message)
    await db.commit()
    return await page(request, db, session, notice=f"Anfrage von {asked.email} abgelehnt.")


# Included after every other router (app.py): the admin forms answer with the page itself,
# so reloading it afterwards asks for the form's address with GET; that leads back to the
# page instead of "405 Method Not Allowed".
reload_router = APIRouter()


@reload_router.get("/admin/{rest:path}", include_in_schema=False)
async def back_to_admin(request: Request, rest: str) -> Response:
    # PARTIAL: a route has this address, only not for GET (a form's POST).
    if any(route.matches(request.scope)[0] == Match.PARTIAL for route in request.app.routes):
        return RedirectResponse("/admin", status_code=303)
    raise HTTPException(404)
