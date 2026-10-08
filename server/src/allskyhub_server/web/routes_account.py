"""Self-service account (roadmap #10): change the password, delete the account."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from allskyhub_protocol import CloseCode
from allskyhub_server.auth import accounts, ratelimit
from allskyhub_server.auth.passwords import verify_secret_async
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.web.deps import CurrentSession, DbSession, SettingsDep, csrf_protect
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])


async def _limit(request: Request, user_id: object) -> None:
    """Every password check counts like a login attempt."""
    try:
        await ratelimit.hit(
            request.app.state.engine, f"account:{user_id}", ratelimit.LOGIN_PER_ACCOUNT
        )
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Zu viele Versuche.", {"Retry-After": str(exc.retry_after)}
        ) from None


async def finish_deletion(request: Request, device_ids: list[str]) -> None:
    """After the commit: delete the cameras' files and close their connections."""
    store: ImageStore = request.app.state.images
    registry: ConnectionRegistry = request.app.state.connections
    for device_id in device_ids:
        await asyncio.to_thread(store.delete_device, device_id)
        await registry.close(device_id, CloseCode.UNPAIRED)


def _page(
    request: Request, session: CurrentSession, status_code: int = 200, **context: object
) -> Response:
    return render(
        request,
        "account.html",
        {
            "keep_choices": accounts.EVENT_KEEP_CHOICES,
            "push_available": request.app.state.notifier.enabled,
            **context,
        },
        session=session,
        status_code=status_code,
    )


@router.get("/account")
async def account(request: Request, session: CurrentSession) -> Response:
    return _page(request, session)


@router.post("/account/events")
async def event_retention(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    keep_days: Annotated[int, Form()],
) -> Response:
    """How long this account's detections are kept (privacy policy)."""
    try:
        accounts.set_event_keep_days(session.user, keep_days)
    except accounts.AccountError as exc:
        return _page(request, session, 400, error=exc.message)
    await db.commit()
    return _page(request, session, notice="Gespeichert.")


@router.post("/account/password")
async def change_password(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    current: Annotated[str, Form(max_length=1024)],
    password: Annotated[str, Form(max_length=1024)],
    password2: Annotated[str, Form(max_length=1024)],
) -> Response:
    await _limit(request, session.user.id)
    user = session.user
    try:
        if password != password2:
            raise accounts.AccountError("Die neuen Passwörter stimmen nicht überein.")
        await accounts.change_password(db, user, current, password, keep_session=session.session_id)
    except accounts.AccountError as exc:
        # change_password checks everything before it changes anything; no rollback needed
        # (it would expire the session's user, which the page still shows).
        return _page(request, session, 400, error=exc.message)
    await db.commit()
    return _page(
        request, session, notice="Passwort geändert. Andere Geräte und die App sind abgemeldet."
    )


@router.post("/account/delete")
async def delete_account(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    session: CurrentSession,
    current: Annotated[str, Form(max_length=1024)],
    confirm: Annotated[bool, Form()] = False,
) -> Response:
    await _limit(request, session.user.id)
    if not confirm or not await verify_secret_async(session.user.password_hash, current):
        return _page(
            request, session, 400, error="Bitte das Passwort eingeben und das Löschen bestätigen."
        )
    device_ids = await accounts.delete_account(db, session.user)
    await db.commit()
    await finish_deletion(request, device_ids)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@router.post("/account/notifications")
async def notifications(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    notify_events: Annotated[bool, Form()] = False,
    notify_offline: Annotated[bool, Form()] = False,
) -> Response:
    """Push notifications in the app (roadmap #6), each off until switched on."""
    session.user.notify_events = notify_events
    session.user.notify_offline = notify_offline
    await db.commit()
    return _page(request, session, notice="Gespeichert.")
