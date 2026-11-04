"""Accounts on the admin page (Benjamin, 2026-11-04)."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.auth import accounts
from allskyhub_server.models import CameraMember, User
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, csrf_from, make_account
from tests.ws_helpers import paired_token

NEW = "testkonto2026"


async def _target(app: FastAPI, email: str) -> str:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await accounts.create_user(db, email, PASSWORD)
        await db.commit()
        return str(user.id)


async def _login(client: httpx.AsyncClient, email: str, password: str) -> int:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return r.status_code


async def test_admin_manages_accounts(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Someone else's camera, for the test account (the store reviewers see a real one).
    dev = FakeDevice()
    await paired_token(new_client, dev)
    admin = await make_account(client)
    assert (await client.get("/admin/users")).status_code == 404  # not an admin yet
    monkeypatch.setattr(settings, "admin_emails", admin)
    target_id = await _target(app, "playreview@example.org")

    listed = await client.get("/admin/users", params={"q": "playreview"})
    assert listed.status_code == 200
    assert "playreview@example.org" in listed.text
    assert admin not in listed.text  # filtered by the search
    page = await client.get(f"/admin/users/{target_id}")
    assert page.status_code == 200
    csrf = csrf_from(page.text)
    base = f"/admin/users/{target_id}"

    # Every action asks for the admin's password.
    r = await client.post(
        f"{base}/password",
        data={"new_password": NEW, "password": "wrong", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert r.status_code == 403
    assert "Dein Passwort stimmt nicht" in r.text
    short = await client.post(
        f"{base}/password",
        data={"new_password": "kurz", "password": PASSWORD, "csrf_token": csrf},
        follow_redirects=True,
    )
    assert "mindestens" in short.text
    r = await client.post(
        f"{base}/password",
        data={"new_password": NEW, "password": PASSWORD, "csrf_token": csrf},
        follow_redirects=True,
    )
    assert "Passwort gesetzt" in r.text
    assert await _login(new_client, "playreview@example.org", NEW) == 200

    # A camera of another owner, shared to the account.
    r = await client.post(
        f"{base}/share",
        data={"device_id": dev.device_id, "role": "viewer", "password": PASSWORD,
              "csrf_token": csrf},
        follow_redirects=True,
    )  # fmt: skip
    assert "freigegeben" in r.text
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        role = await db.scalar(
            select(CameraMember.role).where(CameraMember.device_id == dev.device_id)
        )
    assert role == "viewer"
    assert "1 geteilt" in (await client.get("/admin/users")).text

    # Locked: no sign-in; unlocked again: yes.
    r = await client.post(
        f"{base}/lock", data={"locked": "true", "password": PASSWORD, "csrf_token": csrf},
        follow_redirects=True,
    )  # fmt: skip
    assert "Gesperrt" in r.text
    assert await _login(new_client, "playreview@example.org", NEW) == 401
    assert "gesperrt" in (await client.get("/admin/users", params={"kind": "locked"})).text
    await client.post(
        f"{base}/lock", data={"locked": "false", "password": PASSWORD, "csrf_token": csrf}
    )
    assert await _login(new_client, "playreview@example.org", NEW) == 200

    # Deleting needs the address typed in.
    r = await client.post(
        f"{base}/delete",
        data={"confirm_email": "other@example.org", "password": PASSWORD, "csrf_token": csrf},
        follow_redirects=True,
    )
    assert "stimmt nicht" in r.text
    r = await client.post(
        f"{base}/delete",
        data={"confirm_email": "PlayReview@example.org", "password": PASSWORD,
              "csrf_token": csrf},
        follow_redirects=True,
    )  # fmt: skip
    assert "playreview@example.org gelöscht" in r.text
    async with maker() as db:
        assert await db.scalar(select(User).where(User.email == "playreview@example.org")) is None


async def test_the_own_account_is_changed_elsewhere(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", admin)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        me = await db.scalar(select(User).where(User.email == admin))
    assert me is not None
    page = await client.get(f"/admin/users/{me.id}")
    assert "Das ist dein eigenes Konto" in page.text
    r = await client.post(
        f"/admin/users/{me.id}/lock",
        data={"locked": "true", "password": PASSWORD, "csrf_token": csrf_from(page.text)},
    )
    assert r.status_code == 400
