"""German or English for every web page (Benjamin, 2026-10-22; SharedSky 2026-11-05;
every page bilingual by the owner's decision of 2026-11-27).

Every page exists in German and English: the public pages, the signed-in owner's pages
(cameras, gallery, statistics, account, sharing, streams, Plus), the admin pages and the
privacy policy (the German text is binding, the English one says so). ``?lang=en`` or
``?lang=de`` picks the language and remembers it in the ``ash_lang`` cookie; the menu's
switch goes through ``/sprache/{code}`` and, when signed in, also keeps it as the
account's language for its mails (``User.lang``). German is the default. Templates say
``{{ t('…', '…') }}`` and set ``{% block lang %}``; Python says ``i18n.tr`` (the request's
language, i18n.LanguageMiddleware); German numbers take a decimal comma, English a point.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from allskyhub_server.models import User
from allskyhub_server.web.deps import DbSession, OptionalSession

COOKIE = "ash_lang"
YEAR = 365 * 24 * 3600
router = APIRouter()


def asked(request: Request) -> str | None:
    """The language in the query, if it names one."""
    lang = request.query_params.get("lang")
    return lang if lang in ("de", "en") else None


def is_en(request: Request) -> bool:
    return (asked(request) or request.cookies.get(COOKIE)) == "en"


def remember(request: Request, response: Response, lang: str) -> None:
    if request.cookies.get(COOKIE) != lang:
        response.set_cookie(
            COOKIE,
            lang,
            max_age=YEAR,
            httponly=True,
            secure=request.app.state.settings.session_cookie_secure,
            samesite="lax",
            path="/",
        )


@router.get("/sprache/{lang}")
async def switch(
    request: Request, db: DbSession, session: OptionalSession, lang: str, next: str = "/"
) -> Response:
    """The menu's switch: remember the language and go back (only to a page here)."""
    if lang not in ("de", "en"):
        raise HTTPException(404)
    if session is not None:
        user = await db.get(User, session.user.id)
        if user is not None and user.lang != lang:
            user.lang = lang
            await db.commit()
    target = next if next.startswith("/") and not next.startswith(("//", "/\\")) else "/"
    response = RedirectResponse(target, status_code=303)
    remember(request, response, lang)
    return response
