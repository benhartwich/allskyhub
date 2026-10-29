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
from allskyhub_server.auth import ratelimit
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
    assert "hx-trigger" not in page.text  # nothing on its way: no card polls
    token = csrf_from(page.text)

    def act(app_key: str, action: str) -> dict[str, str]:
        return {"csrf_token": token, "app": app_key, "action": action}

    assert (
        await client.post(follow_redirects=True, url="/admin/apps", data=act("nope", "setup"))
    ).status_code == 400
    assert (
        await client.post(follow_redirects=True, url="/admin/apps", data=act("bortle", "deploy"))
    ).status_code == 400
    r = await client.post(
        follow_redirects=True, url="/admin/apps", data=act("allskyhub", "build-android")
    )
    assert r.status_code == 400
    assert "Erst „Prüfen und einrichten“" in r.text

    hub.ensure_keys(stores.Report(), email)
    r = await client.post(
        follow_redirects=True, url="/admin/apps", data=act("allskyhub", "build-android")
    )
    assert r.status_code == 200, r.text
    assert "Der Server baut gerade" in r.text
    # Post/Redirect/Get (Benjamin, 2026-10-29): reloading shows the page, never re-runs.
    assert [x.status_code for x in r.history] == [303]
    assert "?m=" in str(r.url)
    assert "Der Server baut das App Bundle" in r.text
    page = await client.get(str(r.url))  # the browser's reload
    assert "Der Server baut das App Bundle" not in page.text  # the notice only once
    # While it builds, the card polls itself (no reload of the whole page).
    assert 'hx-get="/admin/apps/card/allskyhub"' in page.text
    assert 'hx-get="/admin/apps/card/bortle"' not in page.text
    card = await client.get("/admin/apps/card/allskyhub")
    assert card.status_code == 200
    assert 'id="app-allskyhub"' in card.text
    assert "<html" not in card.text
    assert "Der Server baut gerade" in card.text
    assert (await client.get("/admin/apps/card/nope")).status_code == 404

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
    r = await client.post(
        follow_redirects=True, url="/admin/apps", data=act("allskyhub", "review-play")
    )
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
    # The limit's windows are fixed hours: here a day, so a test across the full hour
    # still counts in one window (CI failed at 20:00, 2026-10-29).
    monkeypatch.setattr(ratelimit, "APK_PER_IP", (ratelimit.Limit(10, 86400),))
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
    # Some 100 MB each: ten an hour per IP (review of #354), the test APK's included.
    for _ in range(9):
        assert (await client.get("/download/vinventory.apk")).status_code == 200
    r = await client.get("/download/vinventory.apk")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0

    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    page = await client.get("/admin/apps")
    assert 'href="/download/vinventory.apk"' in page.text
    assert 'href="/download/bortle.apk"' not in page.text
    token = csrf_from(page.text)
    r = await client.post(
        follow_redirects=True,
        url="/admin/apps",
        data={"csrf_token": token, "app": "bortle", "action": "apk"},
    )
    assert "APK zum Herunterladen holen gestartet" in r.text
    task = hub.jobs["apk-bortle.json"]
    await task
    assert "Kein Google-Play-Dienstkonto" in (await client.get("/admin/apps")).text


async def test_vinventory_test_apk(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Benjamin, 2026-10-29: Google's APK sends to Play; a plain test APK from the server."""
    out = tmp_path / "release"
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub", release_out=out)
    monkeypatch.setattr(app.state, "stores", hub)
    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    page = await client.get("/admin/apps")
    assert "Test-APK" in page.text
    assert page.text.count('value="build-test-apk"') == 1  # Vinventory only
    token = csrf_from(page.text)
    r = await client.post(
        follow_redirects=True,
        url="/admin/apps",
        data={"csrf_token": token, "app": "bortle", "action": "build-test-apk"},
    )
    assert "keine Test-APK" in r.text
    r = await client.post(
        follow_redirects=True,
        url="/admin/apps",
        data={"csrf_token": token, "app": "vinventory", "action": "build-test-apk"},
    )
    assert "baut die Test-APK" in r.text
    assert (
        (tmp_path / "android-release" / "request").read_text().startswith("app=vinventory-test\n")
    )
    vinventory = stores.OTHER_APPS["vinventory"]
    assert hub.test_job(vinventory).state == "requested"
    assert hub.android_job(vinventory).state == "idle"  # not the store build
    assert hub.android_busy()
    with pytest.raises(stores.StoreError):
        hub.request_android(email, vinventory)

    # The root helper built it.
    (tmp_path / "android-release" / "request").unlink()
    done = tmp_path / "release-vinventory-test"
    done.mkdir()
    (done / "state").write_text("ok\n")
    (done / "vinventory-test-41.apk").write_bytes(b"PK\x03\x04test")
    latest = {"build": 41, "version": "0.1.0", "commit": "128ea0c",
              "file": "vinventory-test-41.apk"}  # fmt: skip
    (done / "latest.json").write_text(json.dumps(latest))
    page = await client.get("/admin/apps")
    assert 'href="/download/vinventory-test.apk"' in page.text
    assert "Build 41" in page.text
    r = await client.get("/download/vinventory-test.apk")
    assert r.status_code == 200
    assert r.content == b"PK\x03\x04test"
    assert "vinventory-test-41.apk" in r.headers["content-disposition"]
    assert (await client.get("/download/bortle-test.apk")).status_code == 404
    assert (await client.get("/download/vinventory-test-test.apk")).status_code == 404
    # A latest.json naming another file is not served.
    (done / "latest.json").write_text(json.dumps({**latest, "file": "../store/play.secret"}))
    assert (await client.get("/download/vinventory-test.apk")).status_code == 404


async def test_a_flash_only_for_its_session(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "release"
    monkeypatch.setattr(
        app.state, "stores", stores.Stores(tmp_path / "store", "x/y", release_out=out)
    )
    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    token = csrf_from((await client.get("/admin/apps")).text)
    r = await client.post(
        "/admin/apps", data={"csrf_token": token, "app": "nope", "action": "setup"}
    )
    assert r.status_code == 303
    url = r.headers["location"]
    other = httpx.AsyncClient(transport=client._transport, base_url=client.base_url)  # pyright: ignore[reportPrivateUsage]
    await make_account(other, "zwei@example.org")
    monkeypatch.setattr(settings, "admin_emails", f"{email},zwei@example.org")
    assert "Unbekannte App" not in (await other.get(url)).text
    shown = await client.get(url)
    assert shown.status_code == 400
    assert "Unbekannte App" in shown.text


async def test_which_app_needs_a_new_build(tmp_path: Path) -> None:
    """Benjamin, 2026-10-29: see which apps have a new state and need a new build."""
    out = tmp_path / "release"
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub", release_out=out)
    vinventory = stores.OTHER_APPS["vinventory"]
    done = tmp_path / "release-vinventory"
    done.mkdir()
    (done / "vinventory-32.aab").write_bytes(b"x")
    latest = {"build": 32, "version": "0.1.0", "commit": "20cbf47",
              "built_at": "2026-10-29T09:12:14Z", "file": "vinventory-32.aab"}  # fmt: skip
    (done / "latest.json").write_text(json.dumps(latest))
    hub.vault.add_build({"id": "B1", "platform": "ios", "app": "vinventory",
                         "at": "2026-10-29T08:00:00+00:00"})  # fmt: skip

    def commit(sha: str, date: str) -> dict[str, Any]:
        return {"sha": sha, "commit": {"message": f"{sha}\n\nbody", "committer": {"date": date}}}

    c1, c2 = commit("c1", "2026-10-29T07:00:00Z"), commit("c2", "2026-10-29T11:00:00Z")
    c3, built = commit("c3", "2026-10-29T12:00:00Z"), commit("20cbf47aa", "2026-10-29T08:58:44Z")
    by_path = {"apps/mobile": [c3, built], "packages/shared": [c2], "package-lock.json": [c3, c1]}
    asked: list[dict[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/benhartwich/vinventory/commits"
        asked.append(dict(request.url.params))
        return httpx.Response(200, json=by_path[request.url.params["path"]])

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    since = apps._since(hub, vinventory)  # pyright: ignore[reportPrivateUsage]
    assert since == "2026-10-28T08:00:00+00:00"  # the older build, a day earlier
    commits = await apps.main_commits(client, hub, vinventory, since)
    assert [c["sha"] for c in commits] == ["c3", "c2", "20cbf47aa", "c1"]
    assert commits[0]["title"] == "c3"
    assert {a["path"] for a in asked} == set(vinventory.app_paths)
    assert all(a["sha"] == "main" and a["since"] == since for a in asked)

    assert apps.pending(commits, "20cbf47", "2026-10-29T09:12:14Z") == 2  # Android: c3, c2
    assert apps.pending(commits, "", "2026-10-29T08:00:00+00:00") == 3  # iOS: all after
    assert apps.pending(commits, "c3", "") == 0
    assert apps.pending(None, "c3", "") is None
    assert apps.pending(commits, "", "") is None

    hub.vault.write_json(apps.live_file("vinventory"), {"at": "2026-10-29T12:30:00+00:00",
                                                         "commits": commits})  # fmt: skip
    card = (await apps.cards(hub, only="vinventory"))[0]
    assert card.pending == {"android": 2, "ios": 3}
    # bortle: the whole repository, no path.
    asked.clear()

    def whole(request: httpx.Request) -> httpx.Response:
        asked.append(dict(request.url.params))
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(whole))
    assert await apps.main_commits(client, hub, stores.OTHER_APPS["bortle"], since) == []
    assert "path" not in asked[0]


async def test_the_last_ios_build_from_codemagic(tmp_path: Path) -> None:
    """Benjamin, 2026-10-29: iOS "unbekannt" for two apps. Android builds had pushed their
    iOS builds out of the hub's list; Codemagic's own list knows them."""
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub")
    hub.vault.add_build(
        {"id": "I1", "platform": "ios", "app": "bortle", "at": "2026-10-20T08:00:00Z"}
    )
    for n in range(12):
        hub.vault.add_build({"id": "local", "platform": "android", "app": "vinventory", "n": n})
    builds = hub.vault.builds()
    assert len(builds) == 11
    assert builds[-1]["id"] == "I1"  # an app's newest per platform stays
    assert builds[0]["n"] == 11
    assert apps._ios_at(hub, "bortle", {}) == "2026-10-20T08:00:00Z"  # pyright: ignore[reportPrivateUsage]

    assert await hub.last_ios_build(stores.OTHER_APPS["bortle"]) is None  # no Codemagic
    hub.vault.put("codemagic", *stores.parse_codemagic("t" * 30), by="a")
    hub.vault.update_meta("codemagic", app_id="APP1", bortle_app_id="APP2")

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/builds"
        assert request.url.params["appId"] == "APP2"
        ios, fin = "ios-release", "finished"
        return httpx.Response(200, json={"builds": [
            # A codemagic.yaml workflow: its name in fileWorkflowId.
            {"workflowId": "6ae1", "fileWorkflowId": ios, "status": fin,
             "startedAt": "2026-10-28T10:00:00.000+0000"},
            {"workflowId": ios, "status": "failed", "startedAt": "2026-10-29T10:00:00.000+0000"},
            {"workflowId": "android", "status": fin, "startedAt": "2026-10-29T11:00:00.000+0000"},
            {"workflowId": ios, "status": fin, "startedAt": "2026-10-27T10:00:00.000+0000"},
        ]})  # fmt: skip

    hub._client = httpx.AsyncClient(transport=httpx.MockTransport(answer))  # pyright: ignore[reportPrivateUsage]
    built = await hub.last_ios_build(stores.OTHER_APPS["bortle"])
    assert built == "2026-10-28T10:00:00.000+0000"
    # Codemagic's record wins over the hub's.
    assert apps._ios_at(hub, "bortle", {"ios_built": built}) == built  # pyright: ignore[reportPrivateUsage]
    commits = [{"sha": "c2", "date": "2026-10-29T09:00:00Z", "title": ""},
               {"sha": "c1", "date": "2026-10-27T09:00:00Z", "title": ""}]  # fmt: skip
    hub.vault.write_json(
        apps.live_file("bortle"), {"at": "x", "ios_built": built, "commits": commits}
    )
    card = (await apps.cards(hub, only="bortle"))[0]
    assert card.pending["ios"] == 1
