"""Owner sign-up (Benjamin, 2026-11-05, SharedSky): a free account on /konto/anlegen behind
Cloudflare Turnstile, active after the link in a confirmation mail; then the owner pairs
a camera or connects a station like any invited account.

Off unless ``open_signup`` is set and Turnstile and mail work. Like the reporters' sign-up
(auth/reporters.py) it never tells whether an address has an account: the page always
says a mail is on its way, and an existing account gets a mail saying so instead.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import Response

from allskyhub_server.auth import accounts, ratelimit, reporters
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.web.deps import DbSession, SettingsDep, client_ip
from allskyhub_server.web.render import render
from allskyhub_server.web.routes_auth import start_session
from allskyhub_server.web.routes_report import SIGNUP_CSP

router = APIRouter()

KIND = "owner"


def _enabled(request: Request, settings: SettingsDep) -> Mailer:
    mailer: Mailer = request.app.state.mailer
    if not settings.open_signup or not request.app.state.turnstile.enabled or not mailer.enabled:
        raise HTTPException(404)
    return mailer


def _page(request: Request, status: int = 200, **context: object) -> Response:
    response = render(
        request,
        "signup.html",
        {"site_key": request.app.state.turnstile.site_key, **context},
        status_code=status,
    )
    response.headers["Content-Security-Policy"] = SIGNUP_CSP
    return response


@router.get("/konto/anlegen")
async def signup_form(request: Request, settings: SettingsDep) -> Response:
    _enabled(request, settings)
    return _page(request)


@router.post("/konto/anlegen")
async def signup(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=1024)],
    terms: Annotated[bool, Form()] = False,
    turnstile: Annotated[str, Form(alias="cf-turnstile-response", max_length=2048)] = "",
) -> Response:
    mailer = _enabled(request, settings)
    ip = client_ip(request, settings)
    try:
        await ratelimit.hit(
            request.app.state.engine, f"owner-signup:{ip}", ratelimit.OWNER_SIGNUP_PER_IP
        )
    except ratelimit.RateLimitedError:
        return _page(request, 429, error="Zu viele Versuche. Bitte später erneut.")
    if not terms:
        return _page(request, 400, email=email,
                     error="Bitte AGB und Datenschutzerklärung bestätigen.")  # fmt: skip
    if not await request.app.state.turnstile.verify(turnstile, ip):
        error = "Die Prüfung gegen Bots hat nicht geklappt. Bitte noch einmal."
        return _page(request, 400, email=email, error=error)
    try:
        user, token = await reporters.sign_up(db, email, password, kind=KIND)
    except accounts.AccountError as exc:
        return _page(request, 400, email=email, error=exc.message)
    await db.commit()
    base = settings.base_url.rstrip("/")
    if token is not None:
        link = f"{base}/konto/bestaetigen?{urlencode({'token': token})}"
        body = (
            "Hallo,\n\nmit diesem Link bestätigst du dein allskyhub-Konto (zwei Tage "
            f"gültig):\n\n{link}\n\nDanach kannst du eine allskyhub-Kamera koppeln oder deine "
            "Allsky-Kamera mit SharedSky verbinden.\n\nWarst du das nicht, ignoriere diese "
            "Nachricht; ohne Bestätigung wird das Konto gelöscht.\n\nallskyhub\n"
        )
    else:
        body = (
            "Hallo,\n\nfür diese Adresse gibt es schon ein allskyhub-Konto. Melde dich damit "
            f"an:\n\n{base}/login\n\nPasswort vergessen? Hier legst du ein neues fest:\n\n"
            f"{base}/password/forgot\n\nallskyhub\n"
        )
    await mailer.send(Mail(to=user.email, subject="allskyhub: dein Konto", body=body))
    return _page(request, sent=True)


@router.get("/konto/bestaetigen")
async def confirm_form(request: Request, db: DbSession, token: str = "") -> Response:
    """A button to confirm: mail scanners fetch links, they do not post forms."""
    ok = await reporters.valid(db, token) is not None
    return render(
        request,
        "signup_confirmed.html",
        {"ask": ok, "token": token},
        status_code=200 if ok else 404,
    )


@router.post("/konto/bestaetigen")
async def confirm(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    token: Annotated[str, Form(max_length=100)],
) -> Response:
    user = await reporters.confirm(db, token)
    if user is None or user.kind != KIND:
        await db.rollback()
        return render(request, "signup_confirmed.html", {"ask": False}, status_code=404)
    await db.commit()
    return await start_session(request, db, settings, user.id, "/start")
