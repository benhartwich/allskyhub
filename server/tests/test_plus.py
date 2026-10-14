"""Plus (roadmap N7): trial month, Paddle checkout and webhooks, cancelling on removal."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.billing import paddle, plus, task
from allskyhub_server.billing.paddle import PaddleError
from allskyhub_server.models import Device, Subscription, User
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import home_csrf
from tests.ws_helpers import paired_token

SECRET = "whsec_test"  # noqa: S105
OTHER = "whsec_other"


class FakePaddle:
    def __init__(self) -> None:
        self.checkouts: list[tuple[str, str]] = []
        self.cancelled: list[str] = []
        self.fail = False

    async def checkout(self, price_id: str, device: Device, user: User) -> str:
        if self.fail:
            raise PaddleError("down")
        self.checkouts.append((price_id, device.id))
        return "txn_01"

    async def portal(self, customer_id: str, subscription_id: str) -> str:
        return f"https://customer-portal.paddle.com/{customer_id}/{subscription_id}"

    async def cancel(self, subscription_id: str) -> None:
        self.cancelled.append(subscription_id)


@pytest.fixture
def billing(app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FakePaddle:
    fake = FakePaddle()
    for name, value in {
        "paddle_api_key": "key",
        "paddle_client_token": "test_token",
        "paddle_webhook_secret": SECRET,
        "paddle_price_month": "pri_month",
        "paddle_price_year": "pri_year",
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(app.state, "paddle", fake)
    return fake


def _signed(body: bytes, at: dt.datetime | None = None, secret: str = SECRET) -> str:
    ts = str(int((at or dt.datetime.now(dt.UTC)).timestamp()))
    h1 = hmac.new(secret.encode(), ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


def _event(
    device_id: str,
    status: str,
    occurred: dt.datetime,
    period_end: dt.datetime | None = None,
    **data: Any,
) -> bytes:
    sub: dict[str, Any] = {
        "id": "sub_01",
        "status": status,
        "customer_id": "ctm_01",
        "custom_data": {"device_id": device_id, "user_id": "not-a-uuid"},
        "items": [{"price": {"id": "pri_year"}, "quantity": 1}],
        "current_billing_period": (
            {"starts_at": occurred.isoformat(), "ends_at": period_end.isoformat()}
            if period_end
            else None
        ),
        **data,
    }
    return json.dumps(
        {
            "event_id": "evt_" + occurred.strftime("%H%M%S%f"),
            "event_type": f"subscription.{'created' if status == 'active' else 'updated'}",
            "occurred_at": occurred.isoformat(),
            "data": sub,
        }
    ).encode()


async def _hook(client: httpx.AsyncClient, body: bytes) -> httpx.Response:
    return await client.post(
        "/paddle/webhook",
        content=body,
        headers={"Paddle-Signature": _signed(body), "Content-Type": "application/json"},
    )


async def _device(app: FastAPI, device_id: str) -> Device:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        return device


def test_signature() -> None:
    now = dt.datetime(2026, 10, 14, 20, tzinfo=dt.UTC)
    body = b'{"a":1}'
    assert paddle.verify(SECRET, _signed(body, now), body, now)
    assert not paddle.verify(SECRET, _signed(body, now), body + b" ", now)
    assert not paddle.verify(SECRET, _signed(body, now, OTHER), body, now)
    # Replayed later than the tolerance.
    assert not paddle.verify(SECRET, _signed(body, now), body, now + dt.timedelta(minutes=6))
    assert not paddle.verify(SECRET, "garbage", body, now)


async def test_offer_and_terms_are_public(client: httpx.AsyncClient) -> None:
    page = await client.get("/plus")
    assert page.status_code == 200
    assert "4,99 €" in page.text
    assert "noch nicht freigeschaltet" in page.text
    terms = await client.get("/agb")
    assert "Widerrufsrecht" in terms.text
    assert "Paddle.com Market Limited" in terms.text
    # Without Paddle set up there is no checkout and no webhook.
    assert (await client.get("/plus/checkout")).status_code == 404
    assert (await client.post("/paddle/webhook", content=b"{}")).status_code == 404


async def test_trial_once_per_account(app: FastAPI, client: httpx.AsyncClient) -> None:
    first, second = FakeDevice(), FakeDevice()
    await paired_token(client, first)
    page = await client.get(f"/cameras/{first.device_id}")
    assert "Einen Monat kostenlos testen" in page.text
    assert "Plus kaufen" not in page.text  # Paddle not set up
    csrf = await home_csrf(client)
    r = await client.post(f"/cameras/{first.device_id}/plus/trial", data={"csrf_token": csrf})
    assert r.status_code == 303
    page = await client.get(f"/cameras/{first.device_id}")
    assert "Probemonat bis" in page.text
    device = await _device(app, first.device_id)
    assert device.plus_until is not None
    left = device.plus_until - dt.datetime.now(dt.UTC)
    assert dt.timedelta(days=29) < left <= plus.TRIAL

    # The same account, a second camera: no second trial.
    from tests.helpers import claim

    reg = await second.register(client)
    assert reg.pairing_code
    await claim(client, reg.pairing_code, name="Dach")
    r = await client.post(f"/cameras/{second.device_id}/plus/trial", data={"csrf_token": csrf})
    assert r.headers["location"].endswith("?plus=trial_used#plus")
    page = await client.get(r.headers["location"])
    assert "schon genutzt" in page.text
    assert (await _device(app, second.device_id)).plus_until is None


async def test_checkout_needs_consent_and_opens_paddle(
    client: httpx.AsyncClient, billing: FakePaddle
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    csrf = await home_csrf(client)
    url = f"/cameras/{dev.device_id}/plus/checkout"
    r = await client.post(url, data={"plan": "year", "csrf_token": csrf})
    assert r.status_code == 400
    assert billing.checkouts == []
    r = await client.post(url, data={"plan": "year", "consent": "true", "csrf_token": csrf})
    assert r.status_code == 200
    assert billing.checkouts == [("pri_year", dev.device_id)]
    assert "cdn.paddle.com" in r.headers["content-security-policy"]
    assert "txn_01" in r.text
    # Every other page keeps the strict policy.
    other = await client.get(f"/cameras/{dev.device_id}")
    assert "paddle" not in other.headers["content-security-policy"]
    assert "Plus kaufen" in other.text

    billing.fail = True
    r = await client.post(url, data={"plan": "month", "consent": "true", "csrf_token": csrf})
    assert r.status_code == 502


async def test_webhooks_set_plus_until(
    app: FastAPI, client: httpx.AsyncClient, billing: FakePaddle
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    year = now + dt.timedelta(days=365)

    bad = _event(dev.device_id, "active", now, year)
    r = await client.post(
        "/paddle/webhook", content=bad, headers={"Paddle-Signature": _signed(bad, secret=OTHER)}
    )
    assert r.status_code == 401

    assert (await _hook(client, _event(dev.device_id, "active", now, year))).status_code == 200
    device = await _device(app, dev.device_id)
    assert device.plus_until == year + plus.RENEWAL_SLACK
    page = await client.get(f"/cameras/{dev.device_id}")
    assert "verlängert sich am" in page.text
    assert "Abo verwalten" in page.text

    # Cancelled at the end of the period: still Plus until then.
    later = now + dt.timedelta(minutes=5)
    change = {"action": "cancel", "effective_at": year.isoformat(), "resume_at": None}
    await _hook(client, _event(dev.device_id, "active", later, year, scheduled_change=change))
    page = await client.get(f"/cameras/{dev.device_id}")
    assert "endet am" in page.text

    # An older event arriving late changes nothing.
    await _hook(client, _event(dev.device_id, "past_due", now - dt.timedelta(hours=1), year))
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        sub = await db.get(Subscription, "sub_01")
        assert sub is not None
        assert sub.status == "active"
        assert sub.cancel_at == year
        assert sub.user_id is None  # custom_data did not name a valid account

    # Cancelled right away (a refund): Plus ends now.
    end = now + dt.timedelta(minutes=10)
    await _hook(client, _event(dev.device_id, "canceled", end, None, canceled_at=end.isoformat()))
    assert (await _device(app, dev.device_id)).plus_until == end

    # The portal link comes from Paddle.
    r = await client.post(
        f"/cameras/{dev.device_id}/plus/manage", data={"csrf_token": await home_csrf(client)}
    )
    assert r.status_code == 404  # no running subscription any more


async def test_removing_the_camera_cancels_its_plus(
    app: FastAPI, client: httpx.AsyncClient, billing: FakePaddle
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    await _hook(client, _event(dev.device_id, "active", now, now + dt.timedelta(days=30)))
    r = await client.post(
        f"/cameras/{dev.device_id}/plus/manage", data={"csrf_token": await home_csrf(client)}
    )
    assert r.headers["location"] == "https://customer-portal.paddle.com/ctm_01/sub_01"

    await client.post(
        f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
    )
    device = await _device(app, dev.device_id)
    assert device.plus_until is None
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await task.cancel_orphans(db, billing)  # type: ignore[arg-type]
    assert billing.cancelled == ["sub_01"]
