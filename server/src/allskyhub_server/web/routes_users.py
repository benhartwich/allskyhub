"""Accounts on the admin page (Benjamin, 2026-11-04: "wo kann ich alle user verwalten?!").

A list with search and filter, and per account: its cameras (own and shared), access
(reset link by mail, sign out everywhere, lock, a password set directly – for the store
test accounts), and deletion. Every change asks for the admin's password again, like
deploys and store keys, and is logged with the admin's address. An admin's own account
is changed on the account page, not here.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.auth import accounts, invitations, password_reset
from allskyhub_server.auth.accounts import AccountError
from allskyhub_server.auth.passwords import hash_secret_async
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.devices import sharing, sightings
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.models import AppToken, CameraMember, Device, User, WebSession
from allskyhub_server.settings import Settings
from allskyhub_server.web import flash
from allskyhub_server.web.deps import CurrentSession, DbSession, SettingsDep, csrf_protect
from allskyhub_server.web.render import render
from allskyhub_server.web.routes_account import finish_deletion
from allskyhub_server.web.routes_admin import admin_only, confirmed

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(csrf_protect)])

KINDS = {"owner": "Kamera-Konto", "reporter": "Melder"}
FILTERS = {"": "alle", "owner": "Kamera-Konten", "reporter": "Melder", "locked": "gesperrt"}
PAGE_SIZE = 200
INVITE_DAYS = 7


@dataclass(frozen=True)
class Row:
    user: User
    own: int
    shared: int
    last_active: dt.datetime | None


async def _rows(db: AsyncSession, query: str, kind: str) -> list[Row]:
    own = select(func.count(Device.id)).where(Device.owner_id == User.id).scalar_subquery()
    shared = (
        select(func.count()).select_from(CameraMember).where(CameraMember.user_id == User.id)
    ).scalar_subquery()
    web = select(func.max(WebSession.last_seen_at)).where(WebSession.user_id == User.id)
    app = select(func.max(AppToken.last_used_at)).where(AppToken.user_id == User.id)
    stmt = select(User, own, shared, web.scalar_subquery(), app.scalar_subquery()).order_by(
        User.created_at.desc()
    )
    if query:
        stmt = stmt.where(User.email.ilike(f"%{_like(query)}%", escape="\\"))
    if kind in KINDS:
        stmt = stmt.where(User.kind == kind)
    elif kind == "locked":
        stmt = stmt.where(User.is_active.is_(False))
    rows = (await db.execute(stmt.limit(PAGE_SIZE))).all()
    return [
        Row(user, int(o or 0), int(s or 0), max((t for t in (w, a) if t), default=None))
        for user, o, s, w, a in rows
    ]


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404)
    return user


def _not_self(session: SessionInfo, user: User) -> None:
    if user.id == session.user.id:
        raise HTTPException(400, "Das eigene Konto bitte unter „Konto“ ändern.")


def _audit(session: SessionInfo, action: str, user: User, **extra: object) -> None:
    log.info(
        "admin account action",
        extra={"admin": session.user.email, "action": action, "account": user.email, **extra},
    )


def _back(
    request: Request, session: SessionInfo, url: str, status: int, **extra: object
) -> Response:
    return flash.redirect(request, session, url, status, dict(extra))


@router.get("/admin/users")
async def users(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    q: str = "",
    kind: str = "",
) -> Response:
    admin_only(session, settings)
    status, kept = flash.take(request, session)
    q = q.strip()[:254]
    rows = await _rows(db, q, kind if kind in FILTERS else "")
    totals: dict[str, int] = {
        kind_: int(count)
        for kind_, count in await db.execute(select(User.kind, func.count()).group_by(User.kind))
    }
    return render(
        request,
        "admin_users.html",
        {
            "rows": rows,
            "q": q,
            "kind": kind,
            "kinds": KINDS,
            "filters": FILTERS,
            "totals": totals,
            "admins": settings.admins,
            "limit": PAGE_SIZE,
            **kept,
        },
        session=session,
        status_code=status,
    )


@router.post("/admin/users/invite")
async def invite(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    email: Annotated[str, Form(max_length=254)],
) -> Response:
    admin_only(session, settings)
    try:
        token = await invitations.create(db, email, dt.timedelta(days=INVITE_DAYS))
    except AccountError as exc:
        return _back(request, session, "/admin/users", 400, error=exc.message)
    await db.commit()
    address = accounts.normalize_email(email)
    link = f"{settings.base_url.rstrip('/')}/invite?{urlencode({'token': token})}"
    mailer: Mailer = request.app.state.mailer
    mail = Mail(
        to=address,
        subject="Einladung zu allskyhub",
        body=(
            "Hallo,\n\ndu bist zu allskyhub eingeladen, dem Hub für Allsky-Kameras. Mit "
            f"diesem Link legst du dein Passwort fest ({INVITE_DAYS} Tage gültig, nur "
            f"einmal):\n\n{link}\n\nallskyhub\n"
        ),
    )
    log.info("admin invited", extra={"admin": session.user.email, "account": address})
    if mailer.enabled and await mailer.send(mail):
        return _back(
            request, session, "/admin/users", 200, notice=f"Einladung an {address} ist raus."
        )
    return _back(
        request, session, "/admin/users", 200,
        notice=f"Die Mail an {address} ging nicht raus. Schick den Link selbst: {link}",
    )  # fmt: skip


@router.get("/admin/users/{user_id}")
async def user_page(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    user_id: uuid.UUID,
) -> Response:
    admin_only(session, settings)
    user = await _user(db, user_id)
    status, kept = flash.take(request, session)
    own = (
        await db.scalars(select(Device).where(Device.owner_id == user.id).order_by(Device.name))
    ).all()
    shared = (
        await db.execute(
            select(Device, CameraMember.role)
            .join(CameraMember, CameraMember.device_id == Device.id)
            .where(CameraMember.user_id == user.id)
            .order_by(Device.name)
        )
    ).all()
    web = await db.execute(
        select(func.count(), func.max(WebSession.last_seen_at)).where(WebSession.user_id == user.id)
    )
    web_count, web_seen = web.one()
    app = await db.execute(
        select(func.count(), func.max(AppToken.last_used_at)).where(AppToken.user_id == user.id)
    )
    app_count, app_seen = app.one()
    cameras = (
        await db.execute(
            select(Device.id, Device.name, User.email)
            .join(User, User.id == Device.owner_id)
            .where(Device.owner_id != user.id)
            .order_by(Device.name)
        )
    ).all()
    return render(
        request,
        "admin_user.html",
        {
            "account": user,
            "kinds": KINDS,
            "is_admin_account": user.email.lower() in settings.admins,
            "is_self": user.id == session.user.id,
            "own": own,
            "shared": shared,
            "web_count": web_count,
            "app_count": app_count,
            "web_seen": web_seen,
            "app_seen": app_seen,
            "cameras": cameras,
            "roles": sharing.ROLES,
            "min_password": accounts.MIN_PASSWORD_LENGTH,
            **kept,
        },
        session=session,
        status_code=status,
    )


async def _checked(
    request: Request, db: AsyncSession, session: SessionInfo, settings: Settings,
    user_id: uuid.UUID, password: str,
) -> tuple[User, str] | Response:  # fmt: skip
    """The account and its page, or the redirect back when the admin's password is wrong."""
    admin_only(session, settings)
    user = await _user(db, user_id)
    url = f"/admin/users/{user.id}"
    if not await confirmed(request, session, password):
        return _back(request, session, url, 403, error="Dein Passwort stimmt nicht.")
    return user, url


async def _sign_out(db: AsyncSession, user: User) -> None:
    await db.execute(delete(WebSession).where(WebSession.user_id == user.id))
    await db.execute(delete(AppToken).where(AppToken.user_id == user.id))


@router.post("/admin/users/{user_id}/share")
async def share(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    user_id: uuid.UUID,
    device_id: Annotated[str, Form(max_length=64)],
    role: Annotated[str, Form(max_length=16)],
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    """A camera for the account, also one the admin does not own (the store test
    accounts see a real camera); the owner sees it under sharing as usual."""
    checked = await _checked(request, db, session, settings, user_id, password)
    if isinstance(checked, Response):
        return checked
    user, url = checked
    device = await db.get(Device, device_id)
    if device is None or device.owner_id is None:
        return _back(request, session, url, 404, error="Diese Kamera gibt es nicht.")
    try:
        await sharing.add(db, device, user.email, role)
    except sharing.SharingError as exc:
        return _back(request, session, url, 400, error=str(exc))
    await db.commit()
    _audit(session, "share", user, device_id=device.id, role=role)
    return _back(
        request, session, url, 200, notice=f"{device.name} ist für {user.email} freigegeben."
    )


@router.post("/admin/users/{user_id}/unshare")
async def unshare(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    user_id: uuid.UUID,
    device_id: Annotated[str, Form(max_length=64)],
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    checked = await _checked(request, db, session, settings, user_id, password)
    if isinstance(checked, Response):
        return checked
    user, url = checked
    await sharing.remove(db, device_id, user.id)
    await db.commit()
    _audit(session, "unshare", user, device_id=device_id)
    return _back(request, session, url, 200, notice="Freigabe entfernt.")


@router.post("/admin/users/{user_id}/reset-link")
async def reset_link(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    user_id: uuid.UUID,
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    checked = await _checked(request, db, session, settings, user_id, password)
    if isinstance(checked, Response):
        return checked
    user, url = checked
    mailer: Mailer = request.app.state.mailer
    if not mailer.enabled:
        return _back(request, session, url, 409, error="E-Mail ist auf diesem Hub aus.")
    created = await password_reset.create(db, user.email)
    await db.commit()
    if created is None:  # pragma: no cover - the account was just read
        raise HTTPException(404)
    _, token = created
    link = f"{settings.base_url.rstrip('/')}/password/reset?{urlencode({'token': token})}"
    sent = await mailer.send(
        Mail(
            to=user.email,
            subject="allskyhub: neues Passwort",
            body=(
                "Hallo,\n\nfür dein allskyhub-Konto wurde ein neues Passwort angelegt. Mit "
                f"diesem Link legst du es fest (eine Stunde gültig, nur einmal):\n\n{link}"
                "\n\nallskyhub\n"
            ),
        )
    )
    _audit(session, "reset_link", user)
    if not sent:
        return _back(
            request, session, url, 502, error="Der Mailserver hat die Mail nicht angenommen."
        )
    return _back(
        request, session, url, 200, notice=f"Link an {user.email} ist raus (1 Stunde gültig)."
    )


@router.post("/admin/users/{user_id}/sign-out")
async def sign_out(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    user_id: uuid.UUID,
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    checked = await _checked(request, db, session, settings, user_id, password)
    if isinstance(checked, Response):
        return checked
    user, url = checked
    _not_self(session, user)
    await _sign_out(db, user)
    await db.commit()
    _audit(session, "sign_out", user)
    return _back(request, session, url, 200, notice="Überall abgemeldet.")


@router.post("/admin/users/{user_id}/lock")
async def lock(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    user_id: uuid.UUID,
    password: Annotated[str, Form(max_length=1024)],
    locked: Annotated[bool, Form()] = True,
) -> Response:
    """A locked account cannot sign in (accounts.authenticate) and is signed out."""
    checked = await _checked(request, db, session, settings, user_id, password)
    if isinstance(checked, Response):
        return checked
    user, url = checked
    _not_self(session, user)
    user.is_active = not locked
    if locked:
        await _sign_out(db, user)
    await db.commit()
    _audit(session, "lock" if locked else "unlock", user)
    return _back(request, session, url, 200, notice="Gesperrt." if locked else "Entsperrt.")


@router.post("/admin/users/{user_id}/password")
async def set_password(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    user_id: uuid.UUID,
    new_password: Annotated[str, Form(max_length=1024)],
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    """Like ``allskyhub-server set-password``: for test accounts whose password the
    stores need. The account is signed out everywhere."""
    checked = await _checked(request, db, session, settings, user_id, password)
    if isinstance(checked, Response):
        return checked
    user, url = checked
    _not_self(session, user)
    if len(new_password) < accounts.MIN_PASSWORD_LENGTH:
        return _back(
            request, session, url, 400,
            error=f"Das neue Passwort braucht mindestens {accounts.MIN_PASSWORD_LENGTH} Zeichen.",
        )  # fmt: skip
    user.password_hash = await hash_secret_async(new_password)
    await _sign_out(db, user)
    await db.commit()
    _audit(session, "set_password", user)
    return _back(request, session, url, 200, notice="Passwort gesetzt, überall abgemeldet.")


@router.post("/admin/users/{user_id}/delete")
async def delete_user(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    user_id: uuid.UUID,
    confirm_email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    checked = await _checked(request, db, session, settings, user_id, password)
    if isinstance(checked, Response):
        return checked
    user, url = checked
    _not_self(session, user)
    if accounts.normalize_email(confirm_email) != user.email:
        return _back(request, session, url, 400, error="Die E-Mail-Adresse stimmt nicht.")
    email = user.email
    photos = await sightings.photo_ids(db, user.id)
    device_ids = await accounts.delete_account(db, user)
    await db.commit()
    await finish_deletion(request, device_ids, photos)
    log.info(
        "admin account action",
        extra={"admin": session.user.email, "action": "delete", "account": email,
               "cameras": len(device_ids)},
    )  # fmt: skip
    return _back(
        request, session, "/admin/users", 200,
        notice=f"{email} gelöscht, {len(device_ids)} Kamera(s) entkoppelt.",
    )  # fmt: skip
