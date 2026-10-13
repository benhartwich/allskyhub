"""The Android test build from the hub's server (deploy/android/README.md).

``deploy/android/allskyhub-android-build.sh`` leaves ``<data_dir>/app-builds/android-test``:
``latest.json`` (build number, version, commit, size, SHA-256) and the APK it names. The test
app asks for ``/app/android/test.json`` and offers an update when the build is newer.
Public: the app is open source, and Android installs an update only with the same key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

from allskyhub_server.web.deps import SettingsDep

router = APIRouter()

APK = re.compile(r"^allskyhub-test-\d{1,9}\.apk$")


def _folder(data_dir: Path) -> Path:
    return data_dir / "app-builds" / "android-test"


def latest(data_dir: Path) -> dict[str, Any] | None:
    try:
        info = json.loads((_folder(data_dir) / "latest.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not APK.match(str(info.get("file", ""))):  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        return None
    return info  # pyright: ignore[reportUnknownVariableType]


@router.get("/app/android/test.json")
async def test_build(settings: SettingsDep) -> Response:
    info = latest(settings.data_dir)
    if info is None:
        raise HTTPException(404)
    keys = ("build", "version", "commit", "built_at", "size", "sha256")
    body = {k: info.get(k) for k in keys} | {"url": f"/app/android/{info['file']}"}
    return JSONResponse(body, headers={"Cache-Control": "no-cache"})


@router.get("/app/android/allskyhub-test.apk")
async def test_apk_latest(settings: SettingsDep) -> Response:
    info = latest(settings.data_dir)
    if info is None:
        raise HTTPException(404)
    return await test_apk(str(info["file"]), settings)


@router.get("/app/android/{name}")
async def test_apk(name: str, settings: SettingsDep) -> Response:
    path = _folder(settings.data_dir) / name
    if not APK.match(name) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=name,
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )
