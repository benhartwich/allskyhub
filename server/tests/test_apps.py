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
