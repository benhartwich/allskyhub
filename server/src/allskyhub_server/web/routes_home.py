"""Start page: the signed-in user's cameras."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from allskyhub_server.web.deps import CurrentSession, csrf_protect
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])


@router.get("/")
async def home(request: Request, session: CurrentSession) -> Response:
    # Cameras arrive with device pairing (SPEC §6.2, M2).
    return render(request, "home.html", {"cameras": []}, session=session)
