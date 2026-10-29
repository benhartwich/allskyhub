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
from allskyhub_server.auth import ratelimit
from allskyhub_server.web.deps import SettingsDep, client_ip

router = APIRouter()

APK = re.compile(r"^allskyhub-test-\d{1,9}\.apk$")


def _folder(android_dir: Path) -> Path:
    return android_dir / "android-test"


async def _limit(request: Request, settings: SettingsDep) -> None:
    """Each APK download counts, per IP and across all (ratelimit.APK_*)."""
    engine = request.app.state.engine
    try:
        await ratelimit.hit(engine, f"apk:ip:{client_ip(request, settings)}", ratelimit.APK_PER_IP)
        await ratelimit.hit(engine, "apk:all", ratelimit.APK_ALL)
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Zu viele Downloads. Bitte später erneut versuchen.",
            {"Retry-After": str(exc.retry_after)},
        ) from None  # fmt: skip


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
async def test_apk_latest(request: Request, settings: SettingsDep) -> Response:
    info = latest(settings.android_dir)
    if info is None:
        raise HTTPException(404)
    return await test_apk(request, str(info["file"]), settings)


@router.get("/app/android/{name}")
async def test_apk(request: Request, name: str, settings: SettingsDep) -> Response:
    path = _folder(settings.android_dir) / name
    if not APK.match(name) or not path.is_file():
        raise HTTPException(404)
    await _limit(request, settings)
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=name,
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@router.get("/download/{key}.apk")
async def store_apk(request: Request, key: str, settings: SettingsDep) -> Response:
    stores: admin_stores.Stores = request.app.state.stores
    test = stores.test_apk(key)  # <key>-test: the server's own build
    found = stores.apk(key)
    if test is not None:
        path, filename = test, test.name
    elif found is not None:
        path, facts = found
        filename = f"{key}-{facts.get('version', '')}-{facts.get('build', '')}.apk"
    else:
        raise HTTPException(404)
    await _limit(request, settings)
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=filename,
        headers={"Cache-Control": "no-cache"},
    )
