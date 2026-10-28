"""Reporter sign-up (app "Himmelshelligkeit melden"): /melden/konto behind Cloudflare
Turnstile, confirmed by mail (auth/reporters.py). Off (404) without Turnstile keys or
without mail."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response

from allskyhub_server.auth import accounts, ratelimit, reporters
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.web.deps import DbSession, SettingsDep, client_ip, csrf_protect
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])

# The sign-up page loads Cloudflare's widget; every other page stays on 'self' only.
SIGNUP_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self' https://challenges.cloudflare.com; "
    "frame-src https://challenges.cloudflare.com; "
    "connect-src 'self' https://challenges.cloudflare.com; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
SIGNUP_PER_IP = (ratelimit.Limit(5, 3600), ratelimit.Limit(20, 86400))


def _enabled(request: Request, settings: SettingsDep) -> Mailer:
    mailer: Mailer = request.app.state.mailer
    if not request.app.state.turnstile.enabled or not mailer.enabled:
        raise HTTPException(404)
    return mailer


def _page(
    request: Request, settings: SettingsDep, status: int = 200, **context: object
) -> Response:
    response = render(
        request,
        "report_signup.html",
        {"site_key": request.app.state.turnstile.site_key, **context},
        status_code=status,
    )
    response.headers["Content-Security-Policy"] = SIGNUP_CSP
    return response


@router.get("/melden/konto")
async def signup_form(request: Request, settings: SettingsDep) -> Response:
    _enabled(request, settings)
    return _page(request, settings)


@router.post("/melden/konto")
async def signup(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=1024)],
    privacy: Annotated[bool, Form()] = False,
    turnstile: Annotated[str, Form(alias="cf-turnstile-response", max_length=2048)] = "",
) -> Response:
    mailer = _enabled(request, settings)
    ip = client_ip(request, settings)
    try:
        await ratelimit.hit(request.app.state.engine, f"report-signup:{ip}", SIGNUP_PER_IP)
    except ratelimit.RateLimitedError:
        return _page(request, settings, 429, error="Zu viele Versuche. Bitte später erneut.")
    if not privacy:
        return _page(request, settings, 400, email=email,
                     error="Bitte die Datenschutzerklärung bestätigen.")  # fmt: skip
    if not await request.app.state.turnstile.verify(turnstile, ip):
        error = "Die Prüfung gegen Bots hat nicht geklappt. Bitte noch einmal."
        return _page(request, settings, 400, email=email, error=error)
    try:
        user, token = await reporters.sign_up(db, email, password)
    except accounts.AccountError as exc:
        return _page(request, settings, 400, email=email, error=exc.message)
    await db.commit()
    base = settings.base_url.rstrip("/")
    if token is not None:
        link = f"{base}/melden/bestaetigen?{urlencode({'token': token})}"
        body = (
            "Hallo,\n\nmit diesem Link bestätigst du dein Melder-Konto bei allskyhub "
            f"(zwei Tage gültig):\n\n{link}\n\nDanach meldest du dich in der App unter "
            "„Mitmachen → Himmelshelligkeit melden“ an.\n\nWarst du das nicht, ignoriere "
            "diese Nachricht; ohne Bestätigung wird das Konto gelöscht.\n\nallskyhub\n"
        )
    else:
        body = (
            "Hallo,\n\nfür diese Adresse gibt es schon ein allskyhub-Konto. Melde dich in der "
            "App einfach damit an. Passwort vergessen? Hier legst du ein neues fest:\n\n"
            f"{base}/password/forgot\n\nallskyhub\n"
        )
    await mailer.send(Mail(to=user.email, subject="allskyhub: dein Melder-Konto", body=body))
    return _page(request, settings, sent=True)


@router.get("/melden/bestaetigen")
async def confirm_form(request: Request, db: DbSession, token: str = "") -> Response:
    """A button to confirm: mail scanners fetch links, they do not post forms."""
    ok = await reporters.valid(db, token) is not None
    return render(
        request,
        "report_confirmed.html",
        {"ask": ok, "ok": ok, "token": token},
        status_code=200 if ok else 404,
    )


@router.post("/melden/bestaetigen")
async def confirm(
    request: Request, db: DbSession, token: Annotated[str, Form(max_length=100)]
) -> Response:
    user = await reporters.confirm(db, token)
    await db.commit()
    return render(
        request,
        "report_confirmed.html",
        {"ok": user is not None},
        status_code=200 if user is not None else 404,
    )
