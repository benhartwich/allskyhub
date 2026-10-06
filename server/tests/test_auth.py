"""Accounts, sessions and CSRF in the web UI."""

from __future__ import annotations

import httpx

from tests.helpers import PASSWORD, csrf_from, signup


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.json() == {"status": "ok"}


async def test_start_page_is_public(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert "Konto anlegen" in r.text
    assert "Meine Kameras" not in r.text


async def test_camera_pages_require_login(client: httpx.AsyncClient) -> None:
    r = await client.get("/cameras/pair")
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/cameras/pair"


async def test_signup_logs_in_and_shows_cameras(client: httpx.AsyncClient) -> None:
    await signup(client)
    r = await client.get("/")
    assert r.status_code == 200
    assert "Noch keine Kamera gekoppelt" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]


async def test_signup_rejects_duplicate_email(client: httpx.AsyncClient) -> None:
    email = await signup(client)
    other = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")  # pyright: ignore[reportPrivateUsage]
    page = await other.get("/signup")
    r = await other.post(
        "/signup",
        data={
            "email": email.upper(),
            "password": PASSWORD,
            "password2": PASSWORD,
            "csrf_token": csrf_from(page.text),
        },
    )
    assert r.status_code == 400
    assert "schon ein Konto" in r.text


async def test_signup_rejects_short_password(client: httpx.AsyncClient) -> None:
    page = await client.get("/signup")
    r = await client.post(
        "/signup",
        data={
            "email": "a@example.org",
            "password": "short",
            "password2": "short",
            "csrf_token": csrf_from(page.text),
        },
    )
    assert r.status_code == 400


async def test_login_logout(client: httpx.AsyncClient) -> None:
    email = await signup(client)
    home = await client.get("/")
    r = await client.post("/logout", data={"csrf_token": csrf_from(home.text)})
    assert r.status_code == 303
    assert "Meine Kameras" not in (await client.get("/")).text

    page = await client.get("/login")
    token = csrf_from(page.text)
    bad = await client.post(
        "/login", data={"email": email, "password": "wrong password", "csrf_token": token}
    )
    assert bad.status_code == 400
    ok = await client.post(
        "/login",
        data={"email": email, "password": PASSWORD, "csrf_token": token, "next": "//evil.org"},
    )
    assert ok.status_code == 303
    assert ok.headers["location"] == "/"
    assert "Meine Kameras" in (await client.get("/")).text


async def test_post_without_csrf_is_rejected(client: httpx.AsyncClient) -> None:
    await client.get("/login")
    r = await client.post("/login", data={"email": "a@example.org", "password": "x"})
    assert r.status_code == 403


async def test_foreign_origin_is_rejected(client: httpx.AsyncClient) -> None:
    page = await client.get("/login")
    r = await client.post(
        "/login",
        data={"email": "a@example.org", "password": "x", "csrf_token": csrf_from(page.text)},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


async def test_signup_can_be_disabled(client: httpx.AsyncClient) -> None:
    settings = client._transport.app.state.settings  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportPrivateUsage, reportUnknownVariableType]
    settings.allow_signup = False
    try:
        assert (await client.get("/signup")).status_code == 404
    finally:
        settings.allow_signup = True
