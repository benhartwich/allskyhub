"""The camera's local UI, signed in through the hub (SPEC §7.4)."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from allskyhub_server.auth.hub_keys import HubKeys
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.devices import local_access, queries
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.models import Device
from allskyhub_server.web.deps import CurrentSession, DbSession, csrf_protect
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])

_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_tasks: set[asyncio.Task[Any]] = set()


def return_base(value: str) -> str | None:
    """SPEC §7.4 A step 2: an absolute http(s) URL without user info, query or fragment,
    at most 500 characters; returned ending in ``/``. None if unusable."""
    if not value or len(value) > 500:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port  # raises on a bad port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password or parts.query or parts.fragment or "?" in value:
        return None
    if any(c.isspace() or c in "<>\"'\\" for c in value):
        return None
    del port
    return value if value.endswith("/") else value + "/"


def _origin(base: str) -> str:
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}"


async def _own(db: DbSession, session: SessionInfo, device_id: str) -> Device:
    device = await queries.owned_one(db, session.user.id, device_id)
    if device is None:
        raise HTTPException(404)
    return device


def _check(return_: str, nonce: str) -> str:
    base = return_base(return_)
    if base is None or not _NONCE.fullmatch(nonce):
        raise HTTPException(400, "Ungültige Anmeldeanfrage der Kamera.")
    return base


@router.get("/cameras/{device_id}/local-login")
async def local_login_ask(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    nonce: str = "",
) -> Response:
    """Step 2: confirm that this camera's local UI may sign in with this account."""
    device = await _own(db, session, device_id)
    return_ = request.query_params.get("return", "")
    base = _check(return_, nonce)
    return render(
        request,
        "local_login.html",
        {"device": device, "base": base, "origin": _origin(base), "nonce": nonce},
        session=session,
    )


@router.post("/cameras/{device_id}/local-login")
async def local_login_confirm(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    return_: Annotated[str, Form(alias="return")],
    nonce: Annotated[str, Form()],
) -> Response:
    """Step 3: back to the camera, the assertion in the fragment (never in a log)."""
    device = await _own(db, session, device_id)
    base = _check(return_, nonce)
    keys: HubKeys = request.app.state.hub_keys
    assertion = keys.sign(
        device_id=device.id, user_id=str(session.user.id), nonce=nonce, now=int(time.time())
    )
    return RedirectResponse(f"{base}local-login/callback#assertion={assertion}", status_code=303)


@router.post("/cameras/{device_id}/local-access")
async def offline_code_create(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """SPEC §7.4 B: a new offline code, shown once."""
    device = await _own(db, session, device_id)
    code = await local_access.create(db, device)
    await db.commit()
    _deliver(request, device.id)
    registry: ConnectionRegistry = request.app.state.connections
    return render(
        request,
        "local_access.html",
        {
            "device": device,
            "code": local_access.shown(code),
            "online": registry.is_online(device.id),
        },
        session=session,
    )


@router.post("/cameras/{device_id}/local-access/remove")
async def offline_code_remove(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    device = await _own(db, session, device_id)
    local_access.remove(device)
    await db.commit()
    _deliver(request, device.id)
    return RedirectResponse(f"/cameras/{device.id}", status_code=303)


def _deliver(request: Request, device_id: str) -> None:
    app = request.app
    task = asyncio.create_task(
        local_access.deliver(app.state.sessionmaker, app.state.connections, device_id)
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
