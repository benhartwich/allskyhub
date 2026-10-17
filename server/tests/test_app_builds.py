"""The Android test build served by the hub (routes_app_builds.py)."""

from __future__ import annotations

import json
import shutil

import httpx

from allskyhub_server.settings import Settings


async def test_latest_build_and_its_apk(client: httpx.AsyncClient, settings: Settings) -> None:
    folder = settings.android_dir / "android-test"
    shutil.rmtree(folder, ignore_errors=True)
    assert (await client.get("/app/android/test.json")).status_code == 404
    assert (await client.get("/app/android/allskyhub-test.apk")).status_code == 404

    folder.mkdir(parents=True)
    (folder / "allskyhub-test-412.apk").write_bytes(b"PK\x03\x04apk")
    info = {"build": 412, "version": "1.0.0", "commit": "abc1234",
            "built_at": "2026-10-13T20:00:00Z", "size": 7, "sha256": "00",
            "file": "allskyhub-test-412.apk"}  # fmt: skip
    (folder / "latest.json").write_text(json.dumps(info))

    r = await client.get("/app/android/test.json")
    assert r.json() == {k: v for k, v in info.items() if k != "file"} | {
        "url": "/app/android/allskyhub-test-412.apk"
    }
    for url in ("/app/android/allskyhub-test.apk", "/app/android/allskyhub-test-412.apk"):
        apk = await client.get(url)
        assert apk.status_code == 200
        assert apk.headers["content-type"] == "application/vnd.android.package-archive"
        assert apk.content == b"PK\x03\x04apk"
    for bad in ("../latest.json", "latest.json", "allskyhub-test-1.apk", "x.apk"):
        assert (await client.get(f"/app/android/{bad}")).status_code == 404, bad

    (folder / "latest.json").write_text(json.dumps(info | {"file": "../../secret"}))
    assert (await client.get("/app/android/test.json")).status_code == 404
