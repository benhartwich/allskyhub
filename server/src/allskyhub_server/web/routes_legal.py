"""Imprint and privacy policy (public)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from allskyhub_server.web import lang
from allskyhub_server.web.deps import OptionalSession
from allskyhub_server.web.render import render

router = APIRouter()


@router.get("/impressum")
async def imprint(request: Request, session: OptionalSession) -> Response:
    return render(request, "impressum.html", session=session)


@router.get("/datenschutz")
async def privacy(request: Request, session: OptionalSession) -> Response:
    return render(
        request,
        "datenschutz.html",
        # The binding German text on ?original=1 (web/lang.py), the language kept.
        {"push_available": request.app.state.notifier.enabled}
        | ({"en": False} if lang.original(request) else {}),
        session=session,
    )
