"""Imprint and privacy policy (public)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

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
        {"push_available": request.app.state.notifier.enabled},
        session=session,
    )
