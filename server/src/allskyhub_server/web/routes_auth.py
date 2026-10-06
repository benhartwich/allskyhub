"""Sign-up, login, logout."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from allskyhub_server.auth import accounts, ratelimit
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


@router.get("/signup")
async def signup_form(
    request: Request, session: OptionalSession, settings: SettingsDep
) -> Response:
    if not settings.allow_signup:
        raise HTTPException(status_code=404)
    if session is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "signup.html")


@router.post("/signup")
async def signup(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=1024)],
    password2: Annotated[str, Form(max_length=1024)],
) -> Response:
    if not settings.allow_signup:
        raise HTTPException(status_code=404)
    try:
        await ratelimit.hit(
            request.app.state.engine,
            f"signup:ip:{client_ip(request, settings)}",
            ratelimit.SIGNUP_PER_IP,
        )
    except ratelimit.RateLimitedError as exc:
        resp = render(
            request,
            "signup.html",
            {"error": "Zu viele neue Konten. Bitte später erneut versuchen.", "email": email},
            status_code=429,
        )
        resp.headers["Retry-After"] = str(exc.retry_after)
        return resp
    try:
        if password != password2:
            raise accounts.AccountError("Die Passwörter stimmen nicht überein.")
        user = await accounts.create_user(db, email, password)
    except accounts.AccountError as exc:
        return render(
            request, "signup.html", {"error": exc.message, "email": email}, status_code=400
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
