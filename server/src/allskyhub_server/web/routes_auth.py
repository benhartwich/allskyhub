"""Login, logout and accepting an invitation (the only way to an account)."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from allskyhub_server.auth import accounts, invitations, ratelimit
from allskyhub_server.auth.sessions import ABSOLUTE_LIFETIME, create_session, delete_session
from allskyhub_server.settings import Settings
from allskyhub_server.web.deps import (
    DbSession,
    OptionalSession,
    SettingsDep,
    client_ip,
    csrf_protect,
)
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])


def safe_next(target: str | None) -> str:
    """Only local paths: ``//evil.example`` and absolute URLs fall back to ``/``."""
    if not target:
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return "/"
    if "\\" in target:
        return "/"
    return target


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=int(ABSOLUTE_LIFETIME.total_seconds()),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


async def _start_session(
    request: Request, db: DbSession, settings: Settings, user_id: uuid.UUID, target: str
) -> Response:
    old = request.cookies.get(settings.session_cookie_name)
    if old:
        await delete_session(db, old)
    token = await create_session(db, user_id)
    await db.commit()
    response = RedirectResponse(safe_next(target), status_code=303)
    _set_session_cookie(response, settings, token)
    return response


@router.get("/login")
async def login_form(request: Request, session: OptionalSession, next: str = "/") -> Response:
    if session is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "login.html", {"next": safe_next(next)})


@router.post("/login")
async def login(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=1024)],
    next: Annotated[str, Form()] = "/",
) -> Response:
    engine = request.app.state.engine
    try:
        await ratelimit.hit(
            engine, f"login:ip:{client_ip(request, settings)}", ratelimit.LOGIN_PER_IP
        )
        await ratelimit.hit(
            engine,
            f"login:acct:{accounts.normalize_email(email)}",
            ratelimit.LOGIN_PER_ACCOUNT,
        )
    except ratelimit.RateLimitedError as exc:
        resp = render(
            request,
            "login.html",
            {"error": "Zu viele Anmeldeversuche. Bitte später erneut versuchen.", "email": email},
            status_code=429,
        )
        resp.headers["Retry-After"] = str(exc.retry_after)
        return resp
    user = await accounts.authenticate(db, email, password)
    if user is None:
        return render(
            request,
            "login.html",
            {"error": "E-Mail-Adresse oder Passwort ist falsch.", "email": email, "next": next},
            status_code=400,
        )
    return await _start_session(request, db, settings, user.id, next)


# The invitation token travels in the query string: never logged (app and nginx log paths only).
@router.get("/invite")
async def invitation_form(
    request: Request, db: DbSession, session: OptionalSession, token: str = ""
) -> Response:
    invitation = await invitations.open_invitation(db, token)
    return render(
        request,
        "invite.html",
        {"invitation": invitation, "token": token},
        session=session,
        status_code=200 if invitation else 404,
    )


@router.post("/invite")
async def accept_invitation(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    token: Annotated[str, Form(max_length=100)],
    password: Annotated[str, Form(max_length=1024)],
    password2: Annotated[str, Form(max_length=1024)],
) -> Response:
    try:
        await ratelimit.hit(
            request.app.state.engine,
            f"invite:ip:{client_ip(request, settings)}",
            ratelimit.INVITE_PER_IP,
        )
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Zu viele Versuche.", {"Retry-After": str(exc.retry_after)}
        ) from None
    try:
        if password != password2:
            raise accounts.AccountError("Die Passwörter stimmen nicht überein.")
        user = await invitations.accept(db, token, password)
    except accounts.AccountError as exc:
        await db.rollback()
        invitation = await invitations.open_invitation(db, token)
        return render(
            request,
            "invite.html",
            {"invitation": invitation, "token": token, "error": exc.message},
            status_code=400,
        )
    return await _start_session(request, db, settings, user.id, "/")


@router.post("/logout")
async def logout(request: Request, db: DbSession, settings: SettingsDep) -> Response:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await delete_session(db, token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response
