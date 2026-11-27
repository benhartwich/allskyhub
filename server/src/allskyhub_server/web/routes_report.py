"""Reporter sign-up (app "Himmelshelligkeit melden"): /melden/konto behind Cloudflare
Turnstile, confirmed by mail (auth/reporters.py). Off (404) without Turnstile keys or
without mail."""

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
        error = tr("Zu viele Versuche. Bitte später erneut.", "Too many tries. Please try later.")
        return _page(request, settings, 429, error=error)
    if not privacy:
        error = tr("Bitte die Datenschutzerklärung bestätigen.",
                   "Please confirm the privacy policy.")  # fmt: skip
        return _page(request, settings, 400, email=email, error=error)
    if not await request.app.state.turnstile.verify(turnstile, ip):
        error = tr("Die Prüfung gegen Bots hat nicht geklappt. Bitte noch einmal.",
                   "The check against bots did not work. Please try again.")  # fmt: skip
        return _page(request, settings, 400, email=email, error=error)
    try:
        user, token = await reporters.sign_up(db, email, password)
    except accounts.AccountError as exc:
        return _page(request, settings, 400, email=email, error=exc.message)
    if token is not None:  # a new or still unconfirmed account: its mails in this language
        user.lang = "en" if english() else "de"
    await db.commit()
    base = settings.base_url.rstrip("/")
    # The mail in the language of the page it came from, and its links keep it.
    lang = {"lang": "en"} if english() else {}
    if token is not None:
        link = f"{base}/melden/bestaetigen?{urlencode({'token': token} | lang)}"
        body = tr(
            "Hallo,\n\nmit diesem Link bestätigst du dein Melder-Konto bei allskyhub "
            f"(zwei Tage gültig):\n\n{link}\n\nDanach meldest du dich in der App unter "
            "„Mitmachen → Himmelshelligkeit melden“ an.\n\nWarst du das nicht, ignoriere "
            "diese Nachricht; ohne Bestätigung wird das Konto gelöscht.\n\nallskyhub\n",
            "Hello,\n\nthis link confirms your reporter account at allskyhub (it works for "
            f"two days):\n\n{link}\n\nThen sign in in the app under “Take part → Report "
            "the sky brightness”.\n\nIf this wasn't you, just ignore this message; without "
            "confirmation the account is deleted.\n\nallskyhub\n",
        )
    else:
        suffix = f"?{urlencode(lang)}" if lang else ""
        body = tr(
            "Hallo,\n\nfür diese Adresse gibt es schon ein allskyhub-Konto. Melde dich in der "
            "App einfach damit an. Passwort vergessen? Hier legst du ein neues fest:\n\n"
            f"{base}/password/forgot\n\nallskyhub\n",
            "Hello,\n\nthere already is an allskyhub account for this address. Just sign in "
            "with it in the app. Forgot your password? Set a new one here:\n\n"
            f"{base}/password/forgot{suffix}\n\nallskyhub\n",
        )
    subject = tr("allskyhub: dein Melder-Konto", "allskyhub: your reporter account")
    await mailer.send(Mail(to=user.email, subject=subject, body=body))
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
