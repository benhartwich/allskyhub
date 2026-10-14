"""Paddle Billing for Plus (roadmap N7).

Paddle sells Plus as merchant of record: it shows the checkout, takes the money, charges
VAT and sends the invoice. The hub only

- creates a transaction for a camera (``custom_data`` names camera and account) and opens
  Paddle's checkout for it (web/routes_plus.py, Paddle.js on that one page),
- learns from Paddle's webhooks what each subscription pays for (:func:`apply`), and
- sends the owner to Paddle's customer portal to cancel or change the card.

Webhooks are signed: ``Paddle-Signature: ts=…;h1=…`` with h1 = HMAC-SHA256 of ``ts:body``
under the endpoint's secret.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import uuid
from typing import Any, cast

import httpx
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.billing import plus
from allskyhub_server.models import Device, Subscription, User
from allskyhub_server.settings import Settings

log = logging.getLogger(__name__)

API = {"sandbox": "https://sandbox-api.paddle.com", "live": "https://api.paddle.com"}
# Paddle signs with its own clock; more than this apart and the event counts as replayed.
TOLERANCE = dt.timedelta(minutes=5)
SUBSCRIPTION_EVENTS = (
    "subscription.created",
    "subscription.updated",
    "subscription.activated",
    "subscription.trialing",
    "subscription.past_due",
    "subscription.paused",
    "subscription.resumed",
    "subscription.canceled",
)


class PaddleError(Exception):
    pass


class Paddle:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=20)

    async def _call(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await self.client.request(
                method,
                API[self.settings.paddle_env] + path,
                json=body,
                headers={"Authorization": f"Bearer {self.settings.paddle_api_key}"},
            )
        except httpx.HTTPError as exc:
            raise PaddleError(f"Paddle nicht erreichbar: {exc}") from None
        if r.status_code >= 400:
            log.warning("paddle %s %s: %s %s", method, path, r.status_code, r.text[:500])
            raise PaddleError(f"Paddle antwortet {r.status_code}.")
        return cast(dict[str, Any], r.json()["data"])

    async def checkout(self, price_id: str, device: Device, user: User) -> str:
        """A transaction for one camera's Plus; returns its id for Paddle.js."""
        data = await self._call(
            "POST",
            "/transactions",
            {
                "items": [{"price_id": price_id, "quantity": 1}],
                "custom_data": {"device_id": device.id, "user_id": str(user.id)},
            },
        )
        return str(data["id"])

    async def portal(self, customer_id: str, subscription_id: str) -> str:
        """A short-lived link to the customer portal for this subscription."""
        data = await self._call(
            "POST",
            f"/customers/{customer_id}/portal-sessions",
            {"subscription_ids": [subscription_id]},
        )
        return str(data["urls"]["general"]["overview"])

    async def cancel(self, subscription_id: str) -> None:
        """Cancel at the end of the paid period (the camera was removed)."""
        await self._call(
            "POST",
            f"/subscriptions/{subscription_id}/cancel",
            {"effective_from": "next_billing_period"},
        )


def verify(secret: str, header: str, body: bytes, now: dt.datetime) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(";") if "=" in p)
    ts, signature = parts.get("ts", ""), parts.get("h1", "")
    if not ts.isdigit() or not signature:
        return False
    if abs(now - dt.datetime.fromtimestamp(int(ts), dt.UTC)) > TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class _Period(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ends_at: dt.datetime


class _Change(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: str
    effective_at: dt.datetime


class _Price(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore")
    price: _Price | None = None


class SubscriptionData(BaseModel):
    """The parts of Paddle's subscription entity the hub uses."""

    model_config = ConfigDict(extra="ignore")
    id: str
    status: str
    customer_id: str
    custom_data: dict[str, Any] | None = None
    current_billing_period: _Period | None = None
    scheduled_change: _Change | None = None
    canceled_at: dt.datetime | None = None
    paused_at: dt.datetime | None = None
    items: list[_Item] = []


class Event(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_id: str
    event_type: str
    occurred_at: dt.datetime
    data: dict[str, Any]


def _uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def apply(db: AsyncSession, event: Event) -> None:
    """Record a subscription event and update the camera's Plus. Commits. Paddle may send
    events twice or out of order: an event older than the stored one changes nothing."""
    if event.event_type not in SUBSCRIPTION_EVENTS:
        return
    data = SubscriptionData.model_validate(event.data)
    sub = await db.get(Subscription, data.id)
    if sub is not None and sub.event_at >= event.occurred_at:
        return
    custom = data.custom_data or {}
    if sub is None:
        device = await db.get(Device, str(custom.get("device_id", "")))
        user_id = _uuid(custom.get("user_id"))
        if device is None:
            log.warning("paddle subscription %s for an unknown camera", data.id)
        sub = Subscription(
            id=data.id,
            device_id=device.id if device is not None else None,
            user_id=user_id if user_id and await db.get(User, user_id) else None,
            customer_id=data.customer_id,
        )
        db.add(sub)
    sub.status = data.status
    sub.customer_id = data.customer_id
    sub.price_id = next((i.price.id for i in data.items if i.price is not None), sub.price_id or "")
    sub.period_end = data.current_billing_period.ends_at if data.current_billing_period else None
    change = data.scheduled_change
    sub.cancel_at = change.effective_at if change and change.action == "cancel" else None
    sub.ended_at = (
        data.canceled_at or data.paused_at or event.occurred_at
        if data.status in ("canceled", "paused")
        else None
    )
    sub.event_at = event.occurred_at
    await db.flush()
    if sub.device_id is not None:
        device = await db.get(Device, sub.device_id)
        if device is not None:
            await plus.refresh(db, device)
    await db.commit()
