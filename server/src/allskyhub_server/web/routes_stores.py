"""Admin: app store accounts and release builds (admin/stores.py).

Uploads need the password again, like a deploy. Nothing a page shows comes from a secret.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from allskyhub_server.admin import appstore
from allskyhub_server.admin import stores as admin_stores
from allskyhub_server.auth.reporters import turnstile_from
from allskyhub_server.devices import youtube
from allskyhub_server.push.fcm import Sender
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
    site_key: Annotated[str, Form(max_length=200)] = "",
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
        elif kind == "github":
            secret, meta = admin_stores.parse_github(token)
        elif kind == "turnstile":
            secret, meta = admin_stores.parse_turnstile(site_key, token)
        elif file is None:
            raise admin_stores.StoreError("Bitte die Datei auswählen.")
        elif kind == "play":
            secret, meta = admin_stores.parse_play(await _read(file))
        elif kind == "fcm":
            secret, meta = admin_stores.parse_fcm(await _read(file))
        elif kind == "youtube":
            secret, meta = admin_stores.parse_youtube(
                await _read(file), youtube.redirect_uri(settings.base_url)
            )
        else:
            secret, meta = admin_stores.parse_asc(await _read(file), key_id, issuer_id)
    except admin_stores.StoreError as exc:
        return await page(request, db, session, 400, error=str(exc))
    stores.vault.put(kind, secret, meta, session.user.email)
    if kind == "youtube":
        notice = "YouTube-OAuth-Client gespeichert. Besitzer können YouTube jetzt verbinden."
    elif kind == "fcm":
        # Takes effect at once, without a restart.
        request.app.state.notifier.set_sender(Sender(secret))
        notice = "Firebase-Dienstkonto gespeichert: Der Hub kann jetzt Push-Nachrichten senden."
    elif kind == "github":
        request.app.state.github.token = request.app.state.latest_image.token = secret.decode()
        request.app.state.github.forget()
        notice = "GitHub-Token gespeichert: Der Hub hat jetzt 5000 Abrufe pro Stunde."
    elif kind == "turnstile":
        request.app.state.turnstile = turnstile_from(settings, stores.vault)
        notice = "Turnstile gespeichert: Die Melder-Registrierung (/melden/konto) ist offen."
    else:
        notice = f"{admin_stores.TITLES[kind]} gespeichert. Jetzt „Prüfen und einrichten“."
    return await page(request, db, session, notice=notice)


@router.post("/admin/stores-fcm-app")
async def fcm_app(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    sender_id: Annotated[str, Form(max_length=200)] = "",
    android_app_id: Annotated[str, Form(max_length=200)] = "",
    android_api_key: Annotated[str, Form(max_length=200)] = "",
    ios_app_id: Annotated[str, Form(max_length=200)] = "",
    ios_api_key: Annotated[str, Form(max_length=200)] = "",
    android_file: Annotated[UploadFile | None, File()] = None,
    ios_files: Annotated[list[UploadFile] | None, File()] = None,
) -> Response:
    """The apps' Firebase registration: public identifiers, shown on the page. From
    google-services.json (it lists every Android app of the project) and each iOS app's
    GoogleService-Info.plist, or typed in. A further app's (bortle) go to its own file,
    for its builds' --dart-define (admin/stores.py ``fcm_defines``)."""
    admin_only(session, settings)
    stores: admin_stores.Stores = request.app.state.stores
    fields = {"sender_id": sender_id, "android_app_id": android_app_id,
              "android_api_key": android_api_key, "ios_app_id": ios_app_id,
              "ios_api_key": ios_api_key}  # fmt: skip
    packages = {
        admin_stores.PACKAGE: "",
        **{a.package: a.key for a in admin_stores.OTHER_APPS.values()},
    }
    found: dict[str, dict[str, str]] = {}  # package -> identifiers from the files
    try:
        if android_file is not None and android_file.filename:
            raw = await _read(android_file)
            for package in packages:
                try:
                    found[package] = admin_stores.parse_google_services(raw, package)
                except admin_stores.StoreError:
                    if package == admin_stores.PACKAGE:
                        raise
        for upload in ios_files or []:
            if not upload.filename:
                continue
            raw = await _read(upload)
            package = admin_stores.plist_package(raw)
            if package not in packages:
                package = admin_stores.PACKAGE  # the error names the app expected
            ids_ios = admin_stores.parse_service_info(raw, package)
            found[package] = found.get(package, {}) | ids_ios
        projects = {v["sender_id"] for v in found.values()}
        if len(projects) > 1:
            raise admin_stores.StoreError("Die Dateien sind aus verschiedenen Projekten.")
        fields |= found.get(admin_stores.PACKAGE, {})
        ids = admin_stores.parse_fcm_app(fields) if fields["sender_id"] or not found else None
    except admin_stores.StoreError as exc:
        return await page(request, db, session, 400, error=str(exc))
    if ids is not None:
        stores.vault.write_json(admin_stores.FCM_APP_FILE, ids)
    others: list[str] = []
    for package, key in packages.items():
        if key and package in found:
            other = admin_stores.OTHER_APPS[key]
            kept = stores.vault.read_json(other.fcm_file) or {}
            stores.vault.write_json(other.fcm_file, kept | found[package])
            others.append(other.title)
    notice = "Firebase-Kennungen der App gespeichert." if ids is not None else ""
    if others:
        notice += (
            f" Firebase-Kennungen für {', '.join(others)} gespeichert; in dessen Builds "
            "kommen sie mit „prüfen und einrichten“ (Codemagic) und beim nächsten Android-Release."
        )
    return await page(request, db, session, notice=notice)


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
        notice=f"{len(data)} Screenshots gespeichert. Jetzt „Android: Play-Eintrag übertragen“.",
    )  # fmt: skip


@router.post("/admin/stores-listing")
async def push_listing(
    request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep
) -> Response:
    admin_only(session, settings)
    stores: admin_stores.Stores = request.app.state.stores
    if not stores.start_listing(session.user.email):
        return await page(request, db, session, 409, error="Die Übertragung läuft noch.")
    return await page(
        request, db, session,
        notice="Übertragung zu Google Play gestartet; sie dauert gut eine Minute. Danach die "
        "Seite neu laden, das Ergebnis steht unter „Android: Eintrag bei Google Play“.",
    )  # fmt: skip


@router.post("/admin/stores-appstore")
async def push_appstore(
    request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep
) -> Response:
    admin_only(session, settings)
    stores: admin_stores.Stores = request.app.state.stores
    if not appstore.start(stores, session.user.email):
        return await page(request, db, session, 409, error="Die Übertragung läuft noch.")
    return await page(
        request, db, session,
        notice="Übertragung zum App Store gestartet; sie dauert ein bis zwei Minuten. Danach "
        "die Seite neu laden, das Ergebnis steht unter „iOS: App Store und TestFlight“.",
    )  # fmt: skip


@router.post("/admin/stores-setup")
async def setup_stores(
    request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep
) -> Response:
    admin_only(session, settings)
    stores: admin_stores.Stores = request.app.state.stores
    report = await stores.setup(session.user.email)
    return await page(request, db, session, store_report=report)


@router.post("/admin/stores-other")
async def other_app(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    app: Annotated[str, Form(max_length=32)],
    action: Annotated[str, Form(max_length=16)],
) -> Response:
    """A further iOS app on the same accounts (admin/stores.py ``OTHER_APPS``)."""
    admin_only(session, settings)
    other = admin_stores.OTHER_APPS.get(app)
    if other is None or action not in ("setup", "build", "android", "play", "appstore"):
        return await page(request, db, session, 400, error="Unbekannte App.")
    stores: admin_stores.Stores = request.app.state.stores
    if action in ("play", "appstore"):
        started = (
            stores.start_listing(session.user.email, other)
            if action == "play"
            else appstore.start(stores, session.user.email, other)
        )
        where = "Google Play" if action == "play" else "App Store Connect"
        notice = (
            f"{other.title}: Eintrag wird an {where} übertragen; das Ergebnis steht gleich hier."
            if started
            else f"{other.title}: Eine Übertragung läuft noch."
        )
        return await page(request, db, session, notice=notice)
    if action == "setup":
        report = await stores.setup_other(other, session.user.email)
        return await page(request, db, session, store_report=report)
    platform = "android" if action == "android" else "ios"
    try:
        await stores.build(platform, session.user.email, app=other)
    except admin_stores.StoreError as exc:
        return await page(request, db, session, 400, error=str(exc))
    notice = (
        f"{other.title}: Android-Release angefordert; der Server baut es und lädt es zu "
        "Google Play hoch."
        if platform == "android"
        else f"{other.title}: iOS-Release bei Codemagic gestartet."
    )
    return await page(request, db, session, notice=notice)


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
