"""Plus (roadmap N7): the offer, the terms, the trial month, buying and managing it.

Paddle.js runs only on the checkout page, which therefore gets its own content security
policy; every other page keeps the hub's strict one.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError

from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.billing import paddle, plus
from allskyhub_server.billing.paddle import Paddle, PaddleError
from allskyhub_server.devices import queries
from allskyhub_server.models import Device, Subscription
from allskyhub_server.settings import Settings
from allskyhub_server.web.deps import (
    CurrentSession,
    DbSession,
    OptionalSession,
    SettingsDep,
    csrf_protect,
)
from allskyhub_server.web.render import render

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(csrf_protect)])
# Paddle's server calls this; it proves itself with the signature, not a session.
webhook_router = APIRouter()

CHECKOUT_CSP = (
    "default-src 'self'; img-src 'self' data: https://*.paddle.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.paddle.com; "
    "script-src 'self' https://cdn.paddle.com; "
    "frame-src https://buy.paddle.com https://sandbox-buy.paddle.com; "
    "connect-src 'self' https://*.paddle.com; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _paddle(request: Request) -> Paddle:
    settings: Settings = request.app.state.settings
    if not settings.billing:
        raise HTTPException(404)
    return request.app.state.paddle


async def _own(db: DbSession, user_id: uuid.UUID, device_id: str) -> Device:
    device = await queries.owned_one(db, user_id, device_id)
    if device is None:
        raise HTTPException(404)
    return device


@router.get("/plus")
async def offer(request: Request, session: OptionalSession, settings: SettingsDep) -> Response:
    return render(
        request,
        "plus.html",
        {
            "price_month": plus.PRICE_MONTH,
            "price_year": plus.PRICE_YEAR,
            "billing": settings.billing,
        },
        session=session,
    )


@router.get("/agb")
async def terms(request: Request, session: OptionalSession) -> Response:
    return render(
        request,
        "agb.html",
        {"price_month": plus.PRICE_MONTH, "price_year": plus.PRICE_YEAR},
        session=session,
    )


@router.post("/cameras/{device_id}/plus/trial")
async def trial(db: DbSession, session: CurrentSession, device_id: str) -> Response:
    device = await _own(db, session.user.id, device_id)
    try:
        await plus.start_trial(db, session.user, device, dt.datetime.now(dt.UTC))
    except plus.PlusError as exc:
        return RedirectResponse(f"/cameras/{device.id}?plus={exc.code}#plus", status_code=303)
    return RedirectResponse(f"/cameras/{device.id}#plus", status_code=303)


@router.post("/cameras/{device_id}/plus/checkout")
async def checkout(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    device_id: str,
    plan: Annotated[Literal["month", "year"], Form()],
    consent: Annotated[bool, Form()] = False,
) -> Response:
    client = _paddle(request)
    device = await _own(db, session.user.id, device_id)
    if not consent:
        raise HTTPException(400, "Bitte AGB und sofortigen Beginn bestätigen.")
    if await plus.running_subscription(db, device.id) is not None:
        return RedirectResponse(f"/cameras/{device.id}#plus", status_code=303)
    price = settings.paddle_price_month if plan == "month" else settings.paddle_price_year
    try:
        transaction = await client.checkout(price, device, session.user)
    except PaddleError:
        raise HTTPException(
            502, "Paddle ist gerade nicht erreichbar. Bitte später noch einmal."
        ) from None
    return _checkout_page(request, settings, session, transaction, device)


@router.get("/plus/checkout")
async def payment_link(
    request: Request, session: OptionalSession, settings: SettingsDep
) -> Response:
    """Paddle's default payment link: Paddle.js opens the transaction named in ``_ptxn``
    (from Paddle's e-mails, e.g. to update the card)."""
    _paddle(request)
    return _checkout_page(request, settings, session, None, None)


def _checkout_page(
    request: Request,
    settings: Settings,
    session: SessionInfo | None,
    transaction: str | None,
    device: Device | None,
) -> Response:
    base = settings.base_url.rstrip("/")
    config = {
        "env": settings.paddle_env,
        "token": settings.paddle_client_token,
        "transaction": transaction,
        "success": f"{base}/cameras/{device.id}?plus=danke#plus" if device else f"{base}/",
    }
    response = render(
        request,
        "plus_checkout.html",
        {"config": json.dumps(config), "device": device},
        session=session,
    )
    response.headers["Content-Security-Policy"] = CHECKOUT_CSP
    return response


@router.post("/cameras/{device_id}/plus/manage")
async def manage(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """Paddle's customer portal for this camera's subscription: cancel, card, invoices."""
    client = _paddle(request)
    device = await _own(db, session.user.id, device_id)
    sub: Subscription | None = await plus.running_subscription(db, device.id)
    if sub is None:
        raise HTTPException(404)
    try:
        url = await client.portal(sub.customer_id, sub.id)
    except PaddleError:
        raise HTTPException(
            502, "Paddle ist gerade nicht erreichbar. Bitte später noch einmal."
        ) from None
    return RedirectResponse(url, status_code=303)


@webhook_router.post("/paddle/webhook")
async def webhook(request: Request, db: DbSession, settings: SettingsDep) -> Response:
    if not settings.billing:
        raise HTTPException(404)
    body = await request.body()
    header = request.headers.get("paddle-signature", "")
    if not paddle.verify(settings.paddle_webhook_secret, header, body, dt.datetime.now(dt.UTC)):
        raise HTTPException(401, "Bad signature")
    try:
        event = paddle.Event.model_validate_json(body)
        await paddle.apply(db, event)
    except ValidationError:
        log.warning("paddle webhook with an unexpected body")
        raise HTTPException(400, "Unexpected body") from None
    return Response(status_code=200)
