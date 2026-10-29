"""The Android test build from the hub's server (deploy/android/README.md).

``deploy/android/allskyhub-android-build.sh`` leaves ``<android_dir>/android-test``, which
belongs to root (the hub only reads it):
``latest.json`` (build number, version, commit, size, SHA-256) and the APK it names. The test
app asks for ``/app/android/test.json`` and offers an update when the build is newer.
Public: the app is open source, and Android installs an update only with the same key.

``/download/<app>.apk`` is the store app itself: Google's universal APK of the newest App
Bundle, fetched on the admin's app page (Stores.fetch_apk), public as Benjamin chose
(2026-10-29).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from allskyhub_server.admin import stores as admin_stores
from allskyhub_server.web.deps import SettingsDep

router = APIRouter()

APK = re.compile(r"^allskyhub-test-\d{1,9}\.apk$")


def _folder(android_dir: Path) -> Path:
    return android_dir / "android-test"


def latest(android_dir: Path) -> dict[str, Any] | None:
    try:
        info = json.loads((_folder(android_dir) / "latest.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not APK.match(str(info.get("file", ""))):  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        return None
    return info  # pyright: ignore[reportUnknownVariableType]


@router.get("/app/android/test.json")
async def test_build(settings: SettingsDep) -> Response:
    info = latest(settings.android_dir)
    if info is None:
        raise HTTPException(404)
    keys = ("build", "version", "commit", "built_at", "size", "sha256")
    body = {k: info.get(k) for k in keys} | {"url": f"/app/android/{info['file']}"}
    return JSONResponse(body, headers={"Cache-Control": "no-cache"})


@router.get("/app/android/allskyhub-test.apk")
async def test_apk_latest(settings: SettingsDep) -> Response:
    info = latest(settings.android_dir)
    if info is None:
        raise HTTPException(404)
    return await test_apk(str(info["file"]), settings)


@router.get("/app/android/{name}")
async def test_apk(name: str, settings: SettingsDep) -> Response:
    path = _folder(settings.android_dir) / name
    if not APK.match(name) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=name,
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@router.get("/download/{key}.apk")
async def store_apk(request: Request, key: str) -> Response:
    stores: admin_stores.Stores = request.app.state.stores
    found = stores.apk(key)
    if found is None:
        raise HTTPException(404)
    path, facts = found
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=f"{key}-{facts.get('version', '')}-{facts.get('build', '')}.apk",
        headers={"Cache-Control": "no-cache"},
    )
