"""Admin: app store accounts and release builds (admin/stores.py).

Uploads need the password again, like a deploy. Nothing a page shows comes from a secret.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from allskyhub_server.admin import stores as admin_stores
from allskyhub_server.web.deps import CurrentSession, DbSession, SettingsDep, csrf_protect
from allskyhub_server.web.routes_admin import admin_only, confirmed, page

router = APIRouter(dependencies=[Depends(csrf_protect)])

WRONG_PASSWORD = "Das Passwort stimmt nicht."  # noqa: S105 - the message


async def _read(upload: UploadFile) -> bytes:
    data = await upload.read(admin_stores.MAX_UPLOAD + 1)
    if len(data) > admin_stores.MAX_UPLOAD:
        raise admin_stores.StoreError("Die Datei ist zu groß für einen Schlüssel.")
    return data


@router.post("/admin/stores/{kind}")
async def upload_store_key(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    kind: str,
    password: Annotated[str, Form(max_length=1024)],
    file: Annotated[UploadFile | None, File()] = None,
    token: Annotated[str, Form(max_length=1024)] = "",
    key_id: Annotated[str, Form(max_length=64)] = "",
    issuer_id: Annotated[str, Form(max_length=64)] = "",
) -> Response:
    admin_only(session, settings)
    if kind not in admin_stores.UPLOADED:
        return await page(request, db, session, 404, error="Unbekannter Zugang.")
    if not await confirmed(request, session, password):
        return await page(request, db, session, 400, error=WRONG_PASSWORD)
    stores: admin_stores.Stores = request.app.state.stores
    try:
        if kind == "codemagic":
            secret, meta = admin_stores.parse_codemagic(token)
        elif file is None:
            raise admin_stores.StoreError("Bitte die Datei auswählen.")
        elif kind == "play":
            secret, meta = admin_stores.parse_play(await _read(file))
        else:
            secret, meta = admin_stores.parse_asc(await _read(file), key_id, issuer_id)
    except admin_stores.StoreError as exc:
        return await page(request, db, session, 400, error=str(exc))
    stores.vault.put(kind, secret, meta, session.user.email)
    return await page(
        request, db, session,
        notice=f"{admin_stores.TITLES[kind]} gespeichert. Jetzt „Prüfen und einrichten“.",
    )  # fmt: skip


@router.get("/admin/stores-android/{name}")
async def android_bundle(
    request: Request, session: CurrentSession, settings: SettingsDep, name: str
) -> Response:
    """The App Bundle built here, for the first upload by hand in the Play Console."""
    admin_only(session, settings)
    stores: admin_stores.Stores = request.app.state.stores
    path = stores.aab(name)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="application/octet-stream", filename=name)


@router.post("/admin/stores-screenshots")
async def upload_screenshots(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    files: Annotated[list[UploadFile], File()],
) -> Response:
    """Phone screenshots for the Play listing; they replace the earlier ones."""
    admin_only(session, settings)
    stores: admin_stores.Stores = request.app.state.stores
    try:
        if len(files) > admin_stores.SCREENSHOTS[1]:
            raise admin_stores.StoreError("Google Play nimmt höchstens acht Screenshots.")
        data: list[bytes] = []
        for f in files:
            raw = await f.read(admin_stores.SCREENSHOT_MAX + 1)
            data.append(raw)
        stores.put_screenshots(data)
    except admin_stores.StoreError as exc:
        return await page(request, db, session, 400, error=str(exc))
    return await page(
        request, db, session,
        notice=f"{len(data)} Screenshots gespeichert. Jetzt „Store-Eintrag übertragen“.",
    )  # fmt: skip


@router.post("/admin/stores-listing")
async def push_listing(
    request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep
) -> Response:
    admin_only(session, settings)
    stores: admin_stores.Stores = request.app.state.stores
    report = await stores.push_listing()
    return await page(request, db, session, store_report=report)


@router.post("/admin/stores-setup")
async def setup_stores(
    request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep
) -> Response:
    admin_only(session, settings)
    stores: admin_stores.Stores = request.app.state.stores
    report = await stores.setup(session.user.email)
    return await page(request, db, session, store_report=report)


@router.post("/admin/stores-build")
async def build_release(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    platform: Annotated[str, Form(max_length=16)],
) -> Response:
    admin_only(session, settings)
    if platform not in admin_stores.PLATFORMS:
        return await page(request, db, session, 400, error="Unbekannte Plattform.")
    stores: admin_stores.Stores = request.app.state.stores
    try:
        await stores.build(platform, session.user.email)
    except admin_stores.StoreError as exc:
        return await page(request, db, session, 400, error=str(exc))
    notice = (
        "Android-Release angefordert: der Server baut es (gut 10 Minuten) und lädt es zu "
        "Google Play hoch."
        if platform == "android"
        else "iOS-Release bei Codemagic gestartet."
    )
    return await page(request, db, session, notice=notice)
