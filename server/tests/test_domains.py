"""Own domains for public pages (roadmap #17)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import domains
from allskyhub_server.models import Device
from tests.helpers import home_csrf
from tests.test_public_page import _public  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("himmel.example.org", True),
        ("HTTPS://Himmel.Example.org/sky", True),
        ("example", False),
        ("192.168.1.1", False),
        ("-bad.example.org", False),
        ("a" * 64 + ".example.org", False),
    ],
)
def test_names(value: str, ok: bool) -> None:
    assert bool(domains.DOMAIN.fullmatch(domains.normalize(value))) is ok


def test_rewrite() -> None:
    assert domains.rewrite("/", "abc") == "/sky/abc"
    assert domains.rewrite("/sky/abc/latest.jpg", "abc") == "/sky/abc/latest.jpg"
    assert domains.rewrite("/static/app.css", "abc") == "/static/app.css"
    assert domains.rewrite("/sky/other", "abc") is None
    assert domains.rewrite("/login", "abc") is None
    assert domains.rewrite("/cameras/x", "abc") is None


async def test_request_and_answers(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_id, slug = await _public(app, client, new_client)
    url = f"/cameras/{device_id}/public/domain"
    spool: Path = app.state.settings.data_dir / "domains"

    async def nowhere(domain: str, base_url: str) -> bool:
        return False

    monkeypatch.setattr(domains, "points_here", nowhere)
    r = await client.post(url, data={"domain": "himmel.example.org",
                                     "csrf_token": await home_csrf(client)})  # fmt: skip
    assert r.status_code == 400
    assert "CNAME" in r.text
    own = await client.post(url, data={"domain": "x.testserver",
                                       "csrf_token": await home_csrf(client)})  # fmt: skip
    assert "nicht die des Hubs" in own.text

    async def here(domain: str, base_url: str) -> bool:
        return True

    monkeypatch.setattr(domains, "points_here", here)
    r = await client.post(url, data={"domain": "Himmel.Example.org",
                                     "csrf_token": await home_csrf(client)})  # fmt: skip
    assert r.status_code == 303
    assert (spool / "himmel.example.org.request").read_text() == "himmel.example.org\n"
    assert "wird eingerichtet" in (await client.get(f"/cameras/{device_id}/public/edit")).text

    # The root helper answers; the hub takes it over and serves the page on that host.
    (spool / "himmel.example.org.ok").write_text("\n")
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await domains.sync(db, app.state.settings.data_dir)
        await db.commit()
        await app.state.hosts.load(db)
        device = await db.get(Device, device_id)
        assert device
        assert device.custom_domain_state == "active"
    custom = {"host": "himmel.example.org"}
    page = await new_client.get("/", headers=custom)
    assert page.status_code == 200
    assert f"/sky/{slug}/latest.jpg" in page.text
    assert (await new_client.get("/login", headers=custom)).status_code == 404
    assert (await new_client.get("/static/app.css", headers=custom)).status_code == 200
    assert (await new_client.get("/login")).status_code == 200  # the hub itself unchanged

    # Removing: a request for the helper, then gone once it answers.
    r = await client.post(f"{url}/remove", data={"csrf_token": await home_csrf(client)})
    assert (spool / "himmel.example.org.remove").is_file()
    (spool / "himmel.example.org.removed").write_text("\n")
    async with maker() as db:
        await domains.sync(db, app.state.settings.data_dir)
        await db.commit()
        device = await db.get(Device, device_id)
        assert device
        assert device.custom_domain is None


async def test_switching_the_page_off_removes_the_domain(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_id, _ = await _public(app, client, new_client)

    async def here(domain: str, base_url: str) -> bool:
        return True

    monkeypatch.setattr(domains, "points_here", here)
    data = {"domain": "sky.example.net", "csrf_token": await home_csrf(client)}
    await client.post(f"/cameras/{device_id}/public/domain", data=data)
    await client.post(f"/cameras/{device_id}/public",
                      data={"enabled": "false", "csrf_token": await home_csrf(client)})  # fmt: skip
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await domains.sync(db, app.state.settings.data_dir)
        await db.commit()
    assert (app.state.settings.data_dir / "domains" / "sky.example.net.remove").is_file()
