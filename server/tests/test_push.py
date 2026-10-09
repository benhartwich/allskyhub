"""Push notifications (roadmap #6): FCM sender, when to notify, tokens and settings."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.models import Device, PushToken, User
from allskyhub_server.push.fcm import Message, Sender
from allskyhub_server.push.notify import Notifier
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, owner_email
from tests.test_events import meteor
from tests.ws_helpers import device_ws, paired_token, send


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[list[str], Message]] = []
        self.dead: set[str] = set()

    async def send(self, tokens: list[str], message: Message) -> list[str]:
        self.sent.append((tokens, message))
        return [t for t in tokens if t in self.dead]


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


async def test_fcm_sender_signs_and_reports_dead_tokens(tmp_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    account = tmp_path / "sa.json"
    account.write_text(
        json.dumps({"project_id": "allskyhub-test", "client_email": "push@test.iam",
                    "private_key": pem})
    )  # fmt: skip
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "oauth2.googleapis.com":
            assertion = dict(x.split("=", 1) for x in request.content.decode().split("&"))[
                "assertion"
            ]
            header, claims, signature = assertion.split(".")
            key.public_key().verify(
                _b64decode(signature),
                f"{header}.{claims}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            assert json.loads(_b64decode(claims))["iss"] == "push@test.iam"
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        body = json.loads(request.content)
        if body["message"]["token"] == "gone-token":  # noqa: S105 - a test value
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND",
                                                       "details": "UNREGISTERED"}})  # fmt: skip
        assert request.headers["authorization"] == "Bearer ya29.test"
        assert body["message"]["notification"]["title"] == "Meteor über Garten"
        return httpx.Response(200, json={"name": "projects/x/messages/1"})

    sender = Sender(account, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    message = Message("Meteor über Garten", "Erkannt um 21:45 Uhr.", {"camera": "c"})
    assert await sender.send(["good-token-123", "gone-token"], message) == ["gone-token"]
    await sender.send(["good-token-123"], message)
    assert sum(r.url.host == "oauth2.googleapis.com" for r in seen) == 1  # token cached
    assert seen[1].url.path == "/v1/projects/allskyhub-test/messages:send"


@pytest.fixture
def fake_push(app: FastAPI) -> FakeSender:
    sender = FakeSender()
    app.state.notifier = Notifier(app.state.sessionmaker, sender)
    return sender


async def _app_login(app: FastAPI, client: httpx.AsyncClient, device_id: str) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, device_id), "password": PASSWORD},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_event_notifications(
    app: FastAPI, client: httpx.AsyncClient, live_app: str, fake_push: FakeSender
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    auth = await _app_login(app, client, dev.device_id)
    reg = {"token": "fcm-token-abcdef", "platform": "android"}
    assert (await client.post("/api/v1/push/tokens", json=reg, headers=auth)).status_code == 204

    async with device_ws(live_app, token) as ws:
        await send(ws, meteor())  # notifications are off by default
        await asyncio.sleep(0.3)
        assert fake_push.sent == []

        r = await client.put("/api/v1/account/settings", json={"notify_events": True}, headers=auth)
        assert r.json()["notify_events"] is True
        assert r.json()["push_available"] is True
        await send(ws, meteor())  # resent after a reconnect: not new, no push
        await asyncio.sleep(0.3)
        assert fake_push.sent == []

        later = meteor()
        later = later.model_copy(
            update={"id": "meteor-20261008T215012Z",
                    "start": later.start + dt.timedelta(minutes=5),
                    "end": later.end + dt.timedelta(minutes=5)}
        )  # fmt: skip
        await send(ws, later)
        for _ in range(100):  # a fixed sleep raced on a loaded machine
            if fake_push.sent:
                break
            await asyncio.sleep(0.05)
        assert len(fake_push.sent) == 1
        tokens, message = fake_push.sent[0]
        assert tokens == ["fcm-token-abcdef"]
        assert message.title.startswith("Meteor über")
        assert message.data["event"] == "meteor-20261008T215012Z"

        third = later.model_copy(update={"id": "meteor-20261008T215112Z"})
        await send(ws, third)  # within 10 minutes: throttled
        await asyncio.sleep(0.3)
        assert len(fake_push.sent) == 1

    removed = await client.post("/api/v1/push/tokens/delete", json=reg, headers=auth)
    assert removed.status_code == 204
    async with app.state.sessionmaker() as db:
        assert (await db.scalars(select(PushToken))).all() == []


async def test_offline_notification_once_per_outage(
    app: FastAPI, client: httpx.AsyncClient, live_app: str, fake_push: FakeSender
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    auth = await _app_login(app, client, dev.device_id)
    await client.post(
        "/api/v1/push/tokens", json={"token": "fcm-token-xyz123", "platform": "android"},
        headers=auth,
    )  # fmt: skip
    await client.put("/api/v1/account/settings", json={"notify_offline": True}, headers=auth)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_seen_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
        )
        await db.commit()
    notifier: Notifier = app.state.notifier
    await notifier.check_offline(app.state.connections)
    await notifier.check_offline(app.state.connections)
    assert [m.title for _, m in fake_push.sent] == ["Garten ist offline"]

    # Back online: a later outage notifies again.
    async with device_ws(live_app, token):
        await asyncio.sleep(0.2)
    async with maker() as db:
        notified = await db.scalar(
            select(Device.offline_notified_at).where(Device.id == dev.device_id)
        )
        assert notified is None


async def test_push_off_without_credentials(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    auth = await _app_login(app, client, dev.device_id)
    got = (await client.get("/api/v1/account/settings", headers=auth)).json()
    assert got["push_available"] is False
    assert "Firebase" not in (await client.get("/datenschutz")).text
    assert "Benachrichtigungen in der App" not in (await client.get("/account")).text


async def test_privacy_and_account_page_with_push(
    app: FastAPI, client: httpx.AsyncClient, fake_push: FakeSender
) -> None:
    from tests.helpers import csrf_from, make_account

    email = await make_account(client)
    assert "Firebase Cloud Messaging" in (await client.get("/datenschutz")).text
    page = await client.get("/account")
    assert "Benachrichtigungen in der App" in page.text
    r = await client.post(
        "/account/notifications",
        data={"notify_offline": "true", "csrf_token": csrf_from(page.text)},
    )
    assert r.status_code == 200
    async with app.state.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        assert (user.notify_events, user.notify_offline) == (False, True)
