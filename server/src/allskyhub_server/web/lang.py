"""German or English for the public pages (Benjamin, 2026-10-22).

Only the pages people reach from outside are in English so far: the start page, asking
for access and the guide for Allsky cameras. ``?lang=en`` or ``?lang=de`` picks the
language and remembers it in the ``ash_lang`` cookie; the menu's switch goes through
``/sprache/{code}``. Every other page stays German.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

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
async def switch(request: Request, lang: str, next: str = "/") -> Response:
    """The menu's switch: remember the language and go back (only to a page here)."""
    if lang not in ("de", "en"):
        raise HTTPException(404)
    target = next if next.startswith("/") and not next.startswith(("//", "/\\")) else "/"
    response = RedirectResponse(target, status_code=303)
    remember(request, response, lang)
    return response
