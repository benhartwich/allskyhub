"""Operator commands: invite, users, set-password, delete-user."""

from __future__ import annotations

import asyncio
import io
import re
import sys
from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI

from allskyhub_protocol import FrameVariant
from allskyhub_server import cli
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, claim, csrf_from, make_account


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Point the CLI's settings at the test database (it reads the environment)."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


def run(*argv: str) -> int:
    return cli.main(list(argv))


async def test_invite_prints_a_working_link(
    cli_env: None, client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _thread(run, "invite", "New@Example.org", "--days", "3") == 0
    out = capsys.readouterr().out
    match = re.search(r"http://testserver/invite\?token=(\S+)", out)
    assert match, out
    page = await client.get("/invite", params={"token": match[1]})
    assert page.status_code == 200
    assert "new@example.org" in page.text


async def test_set_password_and_delete_user(
    cli_env: None,
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    email = await make_account(client)
    dev = FakeDevice()
    reg = await dev.register(new_client)
    assert reg.pairing_code
    await claim(client, reg.pairing_code)
    store: ImageStore = app.state.images
    store.save(dev.device_id, FrameVariant.FULL, b"\xff\xd8\xff")

    monkeypatch.setattr(sys, "stdin", io.StringIO("another good password\n"))
    assert await _thread(run, "set-password", email, "--password-stdin") == 0
    # Signed out everywhere; the new password works, the old one not.
    assert "Meine Kameras" not in (await client.get("/")).text
    page = await client.get("/login")
    for pw, status in ((PASSWORD, 400), ("another good password", 303)):
        r = await client.post(
            "/login", data={"email": email, "password": pw, "csrf_token": csrf_from(page.text)}
        )
        assert r.status_code == status

    assert await _thread(run, "users") == 0
    assert f"1 Kamera(s)  {email}" in capsys.readouterr().out

    assert await _thread(run, "delete-user", email) == 2  # needs --yes
    assert await _thread(run, "delete-user", email, "--yes") == 0
    assert not store.path(dev.device_id, FrameVariant.FULL).exists()
    assert (await dev.token_response(new_client)).status_code == 403
    assert not (await dev.register(new_client)).paired
    assert "Meine Kameras" not in (await client.get("/")).text


async def _thread(fn: Callable[..., int], *args: str) -> int:
    """The CLI runs its own event loop; keep it off the test's loop."""
    return await asyncio.to_thread(fn, *args)
