"""Admin page and deploy requests (admin/deploy.py, web/routes_admin.py)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.admin import deploy
from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import Device
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, csrf_from, make_account, owner_email
from tests.ws_helpers import paired_token

NEW = "b" * 40


class FakeGitHub:
    def __init__(self, ci: str = "success") -> None:
        self.ci = ci

    async def upstream(self, running: str | None) -> deploy.Upstream:
        commit = deploy.Commit(sha=NEW, title="feat(hub): something new", author="x", at=None)
        return deploy.Upstream(sha=NEW, commits=[commit], ahead=1, ci=self.ci, ci_url="")

    def forget(self) -> None:
        pass


def test_ci_state() -> None:
    assert deploy.ci_state([]) == "unknown"
    assert deploy.ci_state([{"status": "completed", "conclusion": "success"}]) == "success"
    assert deploy.ci_state([{"status": "in_progress"}]) == "pending"
    runs = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "failure"},
    ]
    assert deploy.ci_state(runs) == "failure"
    hub_green = [
        {"name": "check (3.11)", "status": "completed", "conclusion": "success"},
        {"name": "build", "status": "in_progress"},  # the Pi image
    ]
    assert deploy.ci_state(hub_green) == "pending"
    assert deploy.ci_state(hub_green, ("check",)) == "success"


def test_job_reads_the_helpers_answer(tmp_path: Path) -> None:
    assert deploy.job(tmp_path).state == "idle"
    folder = deploy.spool(tmp_path)
    folder.mkdir()
    (folder / "state").write_text("failed\n")
    (folder / "target").write_text(NEW + "\n")
    (folder / "log").write_text("\n".join(f"line {i}" for i in range(200)))
    job = deploy.job(tmp_path)
    assert job.state == "failed"
    assert job.target == NEW
    assert job.log.splitlines()[0] == f"line {200 - deploy.LOG_TAIL}"
    (folder / "request").write_text(NEW + "\nme\n")
    assert deploy.job(tmp_path).busy


async def test_only_admins_see_the_page(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = await make_account(client)
    assert (await client.get("/admin")).status_code == 404
    assert 'href="/admin"' not in (await client.get("/account")).text

    monkeypatch.setattr(settings, "admin_emails", f"other@example.org, {email.upper()}")
    monkeypatch.setattr(app.state, "github", FakeGitHub())
    page = await client.get("/admin")
    assert page.status_code == 200
    assert 'href="/admin"' in page.text
    assert "something new" in page.text
    assert "Jetzt deployen" in page.text


async def test_deploy_request(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    monkeypatch.setattr(app.state, "github", FakeGitHub(ci="failure"))
    request_file = deploy.spool(settings.data_dir) / "request"
    request_file.unlink(missing_ok=True)
    csrf = csrf_from((await client.get("/admin")).text)

    r = await client.post(
        "/admin/deploy", data={"sha": NEW, "password": "wrong", "csrf_token": csrf}
    )
    assert r.status_code == 400
    assert "Passwort stimmt nicht" in r.text

    r = await client.post(
        "/admin/deploy", data={"sha": NEW, "password": PASSWORD, "csrf_token": csrf}
    )
    assert r.status_code == 400
    assert "nicht grün" in r.text
    assert not request_file.exists()

    monkeypatch.setattr(app.state, "github", FakeGitHub())
    stale = await client.post(
        "/admin/deploy", data={"sha": "c" * 40, "password": PASSWORD, "csrf_token": csrf}
    )
    assert stale.status_code == 400

    r = await client.post(
        "/admin/deploy", data={"sha": NEW, "password": PASSWORD, "csrf_token": csrf}
    )
    assert r.status_code == 200
    assert request_file.read_text().splitlines() == [NEW, email]
    assert "wartet auf den Server" in r.text

    again = await client.post(
        "/admin/deploy", data={"sha": NEW, "password": PASSWORD, "csrf_token": csrf}
    )
    assert "läuft schon ein Deploy" in again.text
    assert (await client.get("/admin/deploy/job")).status_code == 200
    request_file.unlink()


async def test_mail_test(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    monkeypatch.setattr(app.state, "github", FakeGitHub())
    mailer: MemoryMailer = app.state.mailer
    csrf = csrf_from((await client.get("/admin")).text)
    r = await client.post("/admin/mail-test", data={"csrf_token": csrf})
    assert r.status_code == 200
    assert mailer.sent[-1].to == email


async def test_admin_gifts_plus(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Roadmap N7: an admin gives a camera Plus for good, and can take it back."""
    dev = FakeDevice()
    await paired_token(client, dev)
    monkeypatch.setattr(app.state, "github", FakeGitHub())
    csrf = csrf_from((await client.get("/account")).text)
    gift = {"device_id": dev.device_id, "gift": "true", "csrf_token": csrf}
    # Not an admin: as if the route did not exist.
    assert (await client.post("/admin/plus", data=gift)).status_code == 404

    monkeypatch.setattr(settings, "admin_emails", await owner_email(app, dev.device_id))
    r = await client.post("/admin/plus", data=gift)
    assert r.status_code == 200
    assert "Plus für" in r.text
    assert "geschenkt" in r.text
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device is not None
        assert device.plus_gift

    r = await client.post("/admin/plus", data={"device_id": "nope", "csrf_token": csrf})
    assert r.status_code == 400
    r = await client.post("/admin/plus", data={"device_id": dev.device_id, "csrf_token": csrf})
    assert "Geschenk" in r.text
    assert "Noch keine Kamera mit Plus" in r.text


async def test_admin_sees_all_cameras_without_pictures(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every paired camera with account, profile, version and last contact, no pictures."""
    dev = FakeDevice()
    await paired_token(client, dev)
    monkeypatch.setattr(app.state, "github", FakeGitHub())
    email = await owner_email(app, dev.device_id)
    monkeypatch.setattr(settings, "admin_emails", email)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device is not None
        device.name = "Dachkamera"
        device.last_status = {"update": {"state": "failed", "version": "9.9", "at": "x"}}
        await db.commit()

    page = (await client.get("/admin")).text
    section = page.split('id="kameras"')[1].split("</section>")[0]
    assert "Dachkamera" in section
    assert dev.device_id in section
    assert email in section
    assert "Update fehlgeschlagen" in section
    assert "0 von 1 online" in section
    assert "<img" not in section


async def test_reloading_after_a_form_leads_back_to_the_page(client: httpx.AsyncClient) -> None:
    """The admin forms answer with the page; a reload then asks for the form's address."""
    r = await client.get("/admin/access/01a1730e-3fda-7b6d-8eee-951c53d08de5/invite")
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"
    assert (await client.get("/admin/plus")).headers["location"] == "/admin"
    assert (await client.get("/admin/nothing-here")).status_code == 404


async def test_github_limit_is_named() -> None:
    """The anonymous limit is shared by the whole server: say so, with the reset time."""

    def limited(request: httpx.Request) -> httpx.Response:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793191305"}
        return httpx.Response(403, headers=headers, json={"message": "rate limit"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(limited))
    github = deploy.GitHub("benhartwich/allskyhub", client)
    with pytest.raises(deploy.DeployError, match=r"Abrufgrenze .* wieder ab 12:41 UTC"):
        await github.upstream(None)

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    github = deploy.GitHub(
        "benhartwich/allskyhub", httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    with pytest.raises(deploy.DeployError, match="nicht erreichbar"):
        await github.upstream(None)


async def test_github_token_and_failures_kept(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Benjamin, 2026-10-28: the limit was reached again. A read-only token from the admin
    page lifts it to 5000; a failure is kept for a minute instead of asked again."""
    from allskyhub_server.admin import stores

    seen: list[str | None] = []

    def limited(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})

    github = deploy.GitHub(
        "benhartwich/allskyhub", httpx.AsyncClient(transport=httpx.MockTransport(limited))
    )
    for _ in range(3):
        with pytest.raises(deploy.DeployError, match="mit GitHub-Token 5000"):
            await github.upstream(None)
    assert seen == [None]  # asked once

    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    monkeypatch.setattr(
        app.state, "stores", stores.Stores(tmp_path / "store", "benhartwich/allskyhub")
    )
    monkeypatch.setattr(app.state, "github", github)
    token = csrf_from((await client.get("/admin")).text)
    secret = "github_pat_" + "A1b2" * 10
    r = await client.post(
        "/admin/stores/github", data={"csrf_token": token, "password": PASSWORD, "token": "nope"}
    )
    assert r.status_code == 400
    r = await client.post(
        "/admin/stores/github", data={"csrf_token": token, "password": PASSWORD, "token": secret}
    )
    assert "5000 Abrufe pro Stunde" in r.text
    assert secret not in r.text
    assert seen[-1] == f"Bearer {secret}"  # asked again at once, with the token
    assert app.state.latest_image.token == secret
