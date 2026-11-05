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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response

from allskyhub_server.auth import accounts, ratelimit, reporters
from allskyhub_server.i18n import english, tr
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.web.deps import DbSession, SettingsDep, client_ip, csrf_protect
from allskyhub_server.web.render import render
from allskyhub_server.web.routes_auth import start_session
from allskyhub_server.web.routes_report import SIGNUP_CSP

# CSRF on every form, also the confirmation: else a page could confirm the attacker's own
# account in someone's browser and sign them in there (allsky's review of #392).
router = APIRouter(dependencies=[Depends(csrf_protect)])

KIND = "owner"
# Where the confirmation leads, by a fixed name (never an address from outside): someone
# who came from /mitmachen wants to connect a camera, not build one (Benjamin, 2026-11-05).
NEXT = {"sharedsky": "/start?kamera=ja"}
FIRST_STEPS = "/start"


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
async def signup_form(request: Request, settings: SettingsDep, weiter: str = "") -> Response:
    _enabled(request, settings)
    return _page(request, weiter=weiter if weiter in NEXT else "")


@router.post("/konto/anlegen")
async def signup(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=1024)],
    terms: Annotated[bool, Form()] = False,
    turnstile: Annotated[str, Form(alias="cf-turnstile-response", max_length=2048)] = "",
    weiter: Annotated[str, Form(max_length=20)] = "",
) -> Response:
    mailer = _enabled(request, settings)
    ip = client_ip(request, settings)
    # Kept on every way back to the form, so a second try still leads to SharedSky.
    weiter = weiter if weiter in NEXT else ""
    try:
        await ratelimit.hit(
            request.app.state.engine, f"owner-signup:{ip}", ratelimit.OWNER_SIGNUP_PER_IP
        )
    except ratelimit.RateLimitedError:
        error = tr("Zu viele Versuche. Bitte später erneut.", "Too many tries. Please try later.")
        return _page(request, 429, weiter=weiter, error=error)
    if not terms:
        error = tr("Bitte AGB und Datenschutzerklärung bestätigen.",
                   "Please agree to the terms and the privacy policy.")  # fmt: skip
        return _page(request, 400, email=email, weiter=weiter, error=error)
    if not await request.app.state.turnstile.verify(turnstile, ip):
        error = tr("Die Prüfung gegen Bots hat nicht geklappt. Bitte noch einmal.",
                   "The check against bots did not work. Please try again.")  # fmt: skip
        return _page(request, 400, email=email, weiter=weiter, error=error)
    try:
        user, token = await reporters.sign_up(db, email, password, kind=KIND)
    except accounts.AccountError as exc:
        return _page(request, 400, email=email, weiter=weiter, error=exc.message)
    if token is not None:  # a new or still unconfirmed account: its mails in this language
        user.lang = "en" if english() else "de"
    await db.commit()
    try:  # never a flood of mails to one address, from however many places
        await ratelimit.hit(
            request.app.state.engine, f"signup-mail:{user.email}", ratelimit.SIGNUP_MAIL_PER_EMAIL
        )
    except ratelimit.RateLimitedError:
        return _page(request, sent=True)
    base = settings.base_url.rstrip("/")
    # The mail in the language of the page it came from, and its links keep it.
    lang = {"lang": "en"} if english() else {}
    if token is not None:
        query = {"token": token} | ({"weiter": weiter} if weiter else {}) | lang
        link = f"{base}/konto/bestaetigen?{urlencode(query)}"
        body = tr(
            "Hallo,\n\nmit diesem Link bestätigst du dein allskyhub-Konto (zwei Tage "
            f"gültig):\n\n{link}\n\nDanach kannst du eine allskyhub-Kamera koppeln oder deine "
            "Allsky-Kamera mit SharedSky verbinden.\n\nWarst du das nicht, ignoriere diese "
            "Nachricht; ohne Bestätigung wird das Konto gelöscht.\n\nallskyhub\n",
            "Hello,\n\nthis link confirms your allskyhub account (it works for two days):"
            f"\n\n{link}\n\nThen you can pair an allskyhub camera or connect your all-sky "
            "camera to SharedSky.\n\nIf this wasn't you, just ignore this message; without "
            "confirmation the account is deleted.\n\nallskyhub\n",
        )
    else:
        suffix = f"?{urlencode(lang)}" if lang else ""
        body = tr(
            "Hallo,\n\nfür diese Adresse gibt es schon ein allskyhub-Konto. Melde dich damit "
            f"an:\n\n{base}/login\n\nPasswort vergessen? Hier legst du ein neues fest:\n\n"
            f"{base}/password/forgot\n\nallskyhub\n",
            "Hello,\n\nthere already is an allskyhub account for this address. Sign in with "
            f"it:\n\n{base}/login{suffix}\n\nForgot your password? Set a new one here:\n\n"
            f"{base}/password/forgot{suffix}\n\nallskyhub\n",
        )
    subject = tr("allskyhub: dein Konto", "allskyhub: your account")
    await mailer.send(Mail(to=user.email, subject=subject, body=body))
    return _page(request, sent=True)


@router.get("/konto/bestaetigen")
async def confirm_form(
    request: Request, db: DbSession, token: str = "", weiter: str = ""
) -> Response:
    """A button to confirm: mail scanners fetch links, they do not post forms."""
    ok = await reporters.valid(db, token) is not None
    return render(
        request,
        "signup_confirmed.html",
        {"ask": ok, "token": token, "weiter": weiter if weiter in NEXT else ""},
        status_code=200 if ok else 404,
    )


@router.post("/konto/bestaetigen")
async def confirm(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    token: Annotated[str, Form(max_length=100)],
    weiter: Annotated[str, Form(max_length=20)] = "",
) -> Response:
    user = await reporters.confirm(db, token)
    if user is None or user.kind != KIND:
        await db.rollback()
        return render(request, "signup_confirmed.html", {"ask": False}, status_code=404)
    await db.commit()
    return await start_session(request, db, settings, user.id, NEXT.get(weiter, FIRST_STEPS))
