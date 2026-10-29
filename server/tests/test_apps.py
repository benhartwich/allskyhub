"""The admin's app page (admin/apps.py, web/routes_apps.py): each store's next step."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from allskyhub_server.admin import apps, appstore, stores
from allskyhub_server.admin.stores import AndroidJob
from allskyhub_server.settings import Settings
from tests.helpers import csrf_from, make_account
from tests.test_stores import ISSUER, _p8, _play_json  # pyright: ignore[reportPrivateUsage]


def _job(state: str = "ok", version: str = "1.1.0", at: str = "2026-10-28T14:13:47Z") -> AndroidJob:
    latest = {"build": 323, "version": version, "built_at": at, "file": "allskyhub-323.aab"}
    return AndroidJob(state, "", latest)


def test_android_steps() -> None:
    """Benjamin, 2026-10-28: one next step per store, nothing to puzzle out."""
    row = apps.android_row("1.1.0", False, _job(), None, "")
    assert (row.tone, row.action) == ("todo", "setup")
    assert apps.android_row("1.1.0", True, AndroidJob("running", "", None), None, "").tone == "wait"
    row = apps.android_row("1.1.0", True, AndroidJob("idle", "", None), None, "")
    assert row.action == "build-android"
    row = apps.android_row("1.1.0", True, _job(version="1.0.0"), None, "")
    assert (row.action, row.status) == (
        "build-android",
        "Build 323 ist Version 1.0.0, nicht 1.1.0.",
    )
    row = apps.android_row("1.1.0", True, _job("failed"), None, "")
    assert (row.tone, row.action) == ("fail", "build-android")
    row = apps.android_row("1.1.0", True, _job("built"), None, "/admin/stores-android/a.aab")
    assert (row.action, row.link) == (None, "/admin/stores-android/a.aab")
    row = apps.android_row("1.1.0", True, _job(), None, "")
    assert (row.action, row.label) == ("review-play", "Zur Prüfung einreichen")
    step = {
        "title": "Google Play: Produktion",
        "state": "ok",
        "text": "Version 323 ist eingereicht.",
    }
    sent = {"at": "2026-10-28T15:00:00+00:00", "steps": [step]}
    row = apps.android_row("1.1.0", True, _job(), sent, "")
    assert (row.tone, row.action, row.status) == ("done", None, "Version 323 ist eingereicht.")
    refused = {"at": "2026-10-28T15:00:00+00:00", "steps": [
        {"title": "Google Play: Produktion", "state": "fail", "text": "Nein."}]}  # fmt: skip
    assert apps.android_row("1.1.0", True, _job(), refused, "").action == "review-play"
    # A review from before the newest build does not count for it.
    old = {**sent, "at": "2026-10-27T00:00:00+00:00"}
    assert apps.android_row("1.1.0", True, _job(), old, "").action == "review-play"


def test_rows_follow_what_the_stores_say() -> None:
    """Benjamin, 2026-10-29: rows said "failed" for apps submitted by hand in the
    consoles. What Google and Apple have now comes first."""
    refused = {"at": "2026-10-28T15:00:00+00:00", "steps": [
        {"title": "Google Play: Produktion", "state": "fail", "text": "Nein."}]}  # fmt: skip
    play = {"production": [{"name": "1.1.0", "status": "completed", "codes": ["323"]}]}
    for job in (_job(), _job("failed")):  # a failed later upload of the same build too
        row = apps.android_row("1.1.0", True, job, refused, "", play)
        assert (row.tone, row.action) == ("done", None), row
    draft = {"production": [{"status": "draft", "codes": ["323"]}]}
    assert "Entwurf" in apps.android_row("1.1.0", True, _job(), None, "", draft).status
    other = {"production": [{"status": "completed", "codes": ["322"]}]}
    assert apps.android_row("1.1.0", True, _job(), None, "", other).action == "review-play"
    # A newer version in the repository still asks for a new build.
    assert apps.android_row("1.2.0", True, _job(), None, "", play).action == "build-android"

    build: dict[str, Any] = {"id": "CM", "status": "finished", "at": "2026-10-28T14:09:56+00:00"}
    failed = {"at": "2026-10-28T15:00:00+00:00", "steps": [
        {"title": "Prüfung", "state": "fail", "text": "409"}]}  # fmt: skip
    waiting = [{"version": "1.1.0", "state": "WAITING_FOR_REVIEW"},
               {"version": "1.0", "state": "DEVELOPER_REJECTED"}]  # fmt: skip
    row = apps.ios_row(True, build, None, failed, waiting, "1.1.0")
    assert (row.tone, row.status) == ("done", "Version 1.1.0 wartet bei Apple auf die Prüfung.")
    live = [{"version": "1.1.0", "state": "READY_FOR_SALE"}]
    assert apps.ios_row(True, None, None, None, live, "1.1.0").status.endswith("im App Store.")
    rejected = [{"version": "1.1.0", "state": "REJECTED"}]
    row = apps.ios_row(True, build, None, None, rejected, "1.1.0")
    assert (row.tone, row.action) == ("fail", "review-appstore")
    # Another version waiting does not count for this one.
    assert apps.ios_row(True, None, None, None, waiting, "1.2.0").action == "build-ios"


async def test_live_state_from_both_stores(tmp_path: Path) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t"})
        if path.endswith("/edits") and request.method == "POST":
            return httpx.Response(200, json={"id": "E1"})
        if path.endswith("/edits/E1/tracks"):
            return httpx.Response(200, json={"tracks": [{"track": "production", "releases": [
                {"name": "17", "status": "completed", "versionCodes": ["17"]}]}]})  # fmt: skip
        if path.endswith("/edits/E1") and request.method == "DELETE":
            return httpx.Response(204)
        if path == "/v1/apps":
            return httpx.Response(200, json={"data": [{"id": "A1"}]})
        if path == "/v1/apps/A1/appStoreVersions":
            return httpx.Response(200, json={"data": [{"id": "V1", "attributes": {
                "versionString": "0.3.0", "appVersionState": "IN_REVIEW"}}]})  # fmt: skip
        return httpx.Response(404)

    hub = stores.Stores(
        tmp_path / "store",
        "benhartwich/allskyhub",
        httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )
    assert apps.start_live(hub)  # no keys yet: nothing to ask, the time still recorded
    await hub.jobs[apps.LIVE_JOB]
    assert apps.live_of(hub, "bortle").get("play") is None
    hub.vault.put("play", *stores.parse_play(_play_json()), by="a")
    hub.vault.put("asc", *stores.parse_asc(_p8(), "2X9R4HXF34", ISSUER), by="a")
    assert apps.start_live(hub) is False  # asked a moment ago
    assert apps.start_live(hub, force=True) is True
    await hub.jobs[apps.LIVE_JOB]
    live = apps.live_of(hub, "bortle")
    assert live["play"]["production"] == [{"name": "17", "status": "completed", "codes": ["17"]}]
    assert live["apple"] == [{"version": "0.3.0", "state": "IN_REVIEW"}]

    # Never a Play edit while an App Bundle goes up: it would expire the upload's edit.
    answers = tmp_path / "release-bortle"
    answers.mkdir(parents=True, exist_ok=True)
    (answers / "state").write_text("running\n")
    hub.release_out = tmp_path / "release"
    await apps.refresh_live(hub)
    assert "play" not in apps.live_of(hub, "bortle")
    assert apps.live_of(hub, "bortle")["apple"]  # Apple is still asked

    # Nor while the hub has a Play edit of its own open.
    (answers / "state").write_text("ok\n")
    async with hub.play_edits:
        await apps.refresh_live(hub)
    assert "play" not in apps.live_of(hub, "bortle")
    await apps.refresh_live(hub)
    assert apps.live_of(hub, "bortle")["play"]


def test_ios_steps() -> None:
    build: dict[str, Any] = {"id": "CM", "app_id": "A", "at": "2026-10-28T14:09:56+00:00"}
    assert apps.ios_row(False, None, None, None).action == "setup"
    assert apps.ios_row(True, None, None, None).action == "build-ios"
    row = apps.ios_row(True, {**build, "status": "building"}, None, None)
    assert (row.tone, row.link) == ("wait", "https://codemagic.io/app/A/build/CM")
    assert apps.ios_row(True, {**build, "status": "failed"}, None, None).action == "build-ios"
    done = {**build, "status": "finished"}
    assert apps.ios_row(True, done, None, None).action == "appstore"
    before = {"at": "2026-10-28T13:00:00+00:00", "steps": [{"title": "Build", "state": "ok"}]}
    assert apps.ios_row(True, done, before, None).action == "appstore"  # pushed before the build
    step = {"title": "Build", "state": "warn", "text": "Noch kein fertig verarbeiteter Build"}
    waiting = {"at": "2026-10-28T15:00:00+00:00", "steps": [step]}
    row = apps.ios_row(True, done, waiting, None)
    assert (row.action, "verarbeitet den Build noch" in row.status) == ("appstore", True)
    assert apps.ios_row(True, done, {**waiting, "running": True}, None).tone == "wait"
    pushed = {"at": "2026-10-28T15:00:00+00:00", "steps": [
        {"title": "Build", "state": "ok", "text": "Build 57 gewählt."}]}  # fmt: skip
    row = apps.ios_row(True, done, pushed, None)
    assert (row.action, row.status) == (
        "review-appstore",
        "Build 57 gewählt. Bereit für die Prüfung.",
    )
    text = "Version 1.1.0 mit Build 57 ist zur Prüfung eingereicht."
    sent = {
        "at": "2026-10-28T15:10:00+00:00",
        "steps": [{"title": "Prüfung", "state": "ok", "text": text}],
    }
    assert apps.ios_row(True, done, pushed, sent).tone == "done"
    # An outdated version withdrawn from the queue: the push comes next, not a resubmission.
    back = {**sent, "steps": [{"title": appstore.WITHDRAWN, "state": "warn", "text": "Zurück."}]}
    assert apps.ios_row(True, done, pushed, back).action == "appstore"
    old = {**sent, "steps": [{"title": appstore.OUTDATED, "state": "warn", "text": "Alt."}]}
    assert apps.ios_row(True, done, pushed, old).action == "appstore"


async def test_apps_page_and_actions(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    email = await make_account(client)
    out = tmp_path / "release"
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub", release_out=out)
    monkeypatch.setattr(app.state, "stores", hub)
    assert (await client.get("/admin/apps")).status_code == 404  # admins only
    monkeypatch.setattr(settings, "admin_emails", email)
    page = await client.get("/admin/apps")
    assert page.status_code == 200
    assert 'id="app-allskyhub"' in page.text
    assert 'id="app-bortle"' in page.text
    assert "Version 1.1.0" in page.text
    assert 'http-equiv="refresh"' not in page.text  # nothing builds
    token = csrf_from(page.text)

    def act(app_key: str, action: str) -> dict[str, str]:
        return {"csrf_token": token, "app": app_key, "action": action}

    assert (await client.post("/admin/apps", data=act("nope", "setup"))).status_code == 400
    assert (await client.post("/admin/apps", data=act("bortle", "deploy"))).status_code == 400
    r = await client.post("/admin/apps", data=act("allskyhub", "build-android"))
    assert r.status_code == 400
    assert "Erst „Prüfen und einrichten“" in r.text

    hub.ensure_keys(stores.Report(), email)
    r = await client.post("/admin/apps", data=act("allskyhub", "build-android"))
    assert r.status_code == 200, r.text
    assert "Der Server baut gerade" in r.text
    assert 'http-equiv="refresh"' not in r.text  # never after a POST
    page = await client.get("/admin/apps")
    assert 'http-equiv="refresh"' in page.text  # while it builds, on a GET

    # Built and in the internal test: the next step is the review; its result stays.
    (tmp_path / "android-release" / "request").unlink()
    out.mkdir()
    (out / "state").write_text("ok\n")
    (out / "allskyhub-323.aab").write_bytes(b"x")
    latest = {"build": 323, "version": "1.1.0", "built_at": "2026-10-28T14:13:47Z",
              "size": 1, "file": "allskyhub-323.aab"}  # fmt: skip
    (out / "latest.json").write_text(json.dumps(latest))
    page = await client.get("/admin/apps")
    assert "Build 323 (Version 1.1.0) ist im internen Test." in page.text
    r = await client.post("/admin/apps", data=act("allskyhub", "review-play"))
    assert "Kein Google-Play-Dienstkonto hinterlegt." in r.text
    assert hub.vault.read_json("review-allskyhub-play.json") is not None
    # A warning (here: no account) leaves the step to try again, not a dead end.
    assert "Erneut einreichen" in (await client.get("/admin/apps")).text


async def test_public_apk_download(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Benjamin, 2026-10-29: a public download link for the Android app."""
    out = tmp_path / "release"
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub", release_out=out)
    monkeypatch.setattr(app.state, "stores", hub)
    assert (await client.get("/download/vinventory.apk")).status_code == 404
    hub.apk_dir.mkdir()
    (hub.apk_dir / "vinventory.apk").write_bytes(b"PK\x03\x04apk")
    facts = {"version": "0.1.0", "build": 33, "at": "2026-10-29T12:00:00Z"}
    (hub.apk_dir / "vinventory.json").write_text(json.dumps(facts))
    r = await client.get("/download/vinventory.apk")  # no login
    assert r.status_code == 200
    assert r.content == b"PK\x03\x04apk"
    assert r.headers["content-type"] == "application/vnd.android.package-archive"
    assert "vinventory-0.1.0-33.apk" in r.headers["content-disposition"]
    assert (await client.get("/download/nope.apk")).status_code == 404

    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    page = await client.get("/admin/apps")
    assert 'href="/download/vinventory.apk"' in page.text
    assert 'href="/download/bortle.apk"' not in page.text
    token = csrf_from(page.text)
    r = await client.post(
        "/admin/apps", data={"csrf_token": token, "app": "bortle", "action": "apk"}
    )
    assert "APK zum Herunterladen holen gestartet" in r.text
    task = hub.jobs["apk-bortle.json"]
    await task
    assert "Kein Google-Play-Dienstkonto" in (await client.get("/admin/apps")).text
