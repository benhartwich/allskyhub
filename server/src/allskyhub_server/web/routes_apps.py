"""Admin: the apps and their stores on one page (admin/apps.py), each with its next step.

The accounts themselves (keys, tokens) stay on the admin page; this page only uses them.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response

from allskyhub_server.admin import apps, appstore
from allskyhub_server.admin import stores as admin_stores
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.i18n import english, tr
from allskyhub_server.web import flash
from allskyhub_server.web.deps import CurrentSession, SettingsDep, csrf_protect
from allskyhub_server.web.render import render
from allskyhub_server.web.routes_admin import admin_only, confirmed, your_password_wrong

router = APIRouter(dependencies=[Depends(csrf_protect)])
log = logging.getLogger(__name__)

TITLES = {
    "setup": "Einrichten",
    "build-android": "Android-Release bauen",
    "build-ios": "iOS-Release bauen",
    "play-listing": "Google-Play-Eintrag übertragen",
    "appstore": "App Store übertragen",
    "review-play": "Android zur Prüfung einreichen",
    "review-appstore": "iOS zur Prüfung einreichen",
    "apk": "APK zum Herunterladen holen",
    "build-test-apk": "Test-APK bauen",
    "build-screenshots": "Screenshots erzeugen",
    "take-screenshots": "Screenshots übernehmen",
}
TITLES_EN = {
    "setup": "Set up",
    "build-android": "Build Android release",
    "build-ios": "Build iOS release",
    "play-listing": "Send Google Play listing",
    "appstore": "Send to App Store",
    "review-play": "Submit Android for review",
    "review-appstore": "Submit iOS for review",
    "apk": "Fetch APK for download",
    "build-test-apk": "Build test APK",
    "build-screenshots": "Make screenshots",
    "take-screenshots": "Take over screenshots",
}


def titles() -> dict[str, str]:
    return TITLES_EN if english() else TITLES


async def apps_page(
    request: Request,
    session: SessionInfo,
    status_code: int = 200,
    **extra: object,
) -> Response:
    if request.method == "POST":  # reloading must not run the action again
        return flash.redirect(request, session, "/admin/apps", status_code, extra)
    status_code, kept = flash.take(request, session)
    extra = {**kept, **extra}
    stores: admin_stores.Stores = request.app.state.stores
    cards = await apps.cards(stores)
    return render(
        request,
        "admin_apps.html",
        {
            "cards": cards,
            "titles": titles(),
            "android_busy": stores.android_building(),
            "accounts": [
                e
                for e in stores.vault.entries()
                if e.kind in ("play", "asc", "codemagic", "fcm", "github")
            ],
            **extra,
        },
        session=session,
        status_code=status_code,
    )


@router.get("/admin/apps")
async def apps_view(request: Request, session: CurrentSession, settings: SettingsDep) -> Response:
    admin_only(session, settings)
    apps.start_live(request.app.state.stores)  # the stores' own word, in the background
    return await apps_page(request, session)


@router.get("/admin/apps/card/{key}")
async def app_card(
    request: Request, session: CurrentSession, settings: SettingsDep, key: str
) -> Response:
    """One app's card, polled by itself while something of it is on its way
    (Benjamin, 2026-10-29: "echte aktualisierung nach aktionen")."""
    admin_only(session, settings)
    stores: admin_stores.Stores = request.app.state.stores
    found = await apps.cards(stores, only=key)
    if not found:
        raise HTTPException(404)
    return render(
        request,
        "_app_card.html",
        {"card": found[0], "titles": titles(), "android_busy": stores.android_building()},
        session=session,
    )


@router.post("/admin/apps")
async def apps_action(
    request: Request,
    session: CurrentSession,
    settings: SettingsDep,
    app: Annotated[str, Form(max_length=32)],
    action: Annotated[str, Form(max_length=32)],
) -> Response:
    admin_only(session, settings)
    other = admin_stores.OTHER_APPS.get(app)
    if (app != "allskyhub" and other is None) or action not in apps.ACTIONS:
        return await apps_page(
            request, session, 400, error=tr("Unbekannte App oder Aktion.", "Unknown app or action.")
        )
    if other is not None and not other.stores and action != "build-test-apk":
        return await apps_page(
            request,
            session,
            400,
            error=f"{other.title}: " + tr("nur die Test-APK.", "only the test APK."),
        )
    stores: admin_stores.Stores = request.app.state.stores
    by = session.user.email
    name = other.title if other else "allskyhub"
    try:
        if action == "setup":
            report = await (stores.setup_other(other, by) if other else stores.setup(by))
            return await apps_page(
                request, session, report=report, report_title=f"{name}: {titles()['setup']}"
            )
        if action == "build-screenshots":
            await stores.build("screenshots", by, app=other)
            return await apps_page(request, session, notice=f"{name}: " + tr(
                "Codemagic startet die App im iPhone-Simulator und im Android-Emulator (etwa 20 "
                "Minuten); danach „Screenshots übernehmen“.",
                "Codemagic starts the app in the iPhone simulator and the Android emulator (about "
                '20 minutes); then "Take over screenshots".'))  # fmt: skip
        if action == "take-screenshots":
            report = await stores.take_screenshots(other)
            return await apps_page(
                request,
                session,
                report=report,
                report_title=f"{name}: {titles()['take-screenshots']}",
            )
        if action in ("build-android", "build-ios"):
            platform = action.removeprefix("build-")
            await stores.build(platform, by, app=other)
            notice = (
                tr(
                    "Der Server baut das App Bundle (gut 10 Minuten) und lädt es in den "
                    "internen Test.",
                    "The server builds the App Bundle (a good 10 minutes) and uploads it to "
                    "internal testing.",
                )
                if platform == "android"
                else tr(
                    "Codemagic baut (etwa 20 Minuten) und schickt den Build nach TestFlight.",
                    "Codemagic builds (about 20 minutes) and sends the build to TestFlight.",
                )
            )
            return await apps_page(request, session, notice=f"{name}: {notice}")
        if action == "play-listing":
            started = stores.start_listing(by, other)
        elif action == "appstore":
            started = appstore.start(stores, by, other)
        elif action == "apk":
            started = stores.start_apk(by, other)
        elif action == "build-test-apk":
            if other is None:
                raise admin_stores.StoreError(
                    tr("Für diese App gibt es keine Test-APK.", "This app has no test APK.")
                )
            stores.request_test_apk(by, other)
            return await apps_page(request, session, notice=f"{name}: " + tr(
                "Der Server baut die Test-APK (gut 10 Minuten).",
                "The server builds the test APK (a good 10 minutes)."))  # fmt: skip
        else:
            store = action.removeprefix("review-")
            if store == "play":
                report = await stores.submit_play(other.package if other else admin_stores.PACKAGE)
            else:
                report = await appstore.submit_report(stores, other)
            withdrawn = any(x.title == appstore.WITHDRAWN for x in report.steps)
            # The waiting version now takes the new build.
            if withdrawn and not appstore.start(stores, by, other):
                report.add(titles()["appstore"], "warn", tr(
                    "Eine Übertragung läuft noch; danach „App Store übertragen“ noch einmal "
                    "drücken.",
                    'A transfer is still running; then press "Send to App Store" '
                    "again."))  # fmt: skip
            apps.save_review(stores, other.key if other else "allskyhub", store, by, report)
            log.info(
                "review %s %s submitted by %s: %s", store, name, by,
                [(x.title, x.state, x.text) for x in report.steps],
            )  # fmt: skip
            return await apps_page(
                request, session, report=report, report_title=f"{name}: {titles()[action]}"
            )
    except admin_stores.StoreError as exc:
        return await apps_page(request, session, 400, error=f"{name}: {exc}")
    if not started:
        return await apps_page(
            request,
            session,
            409,
            error=f"{name}: " + tr("Eine Übertragung läuft noch.", "A transfer is still running."),
        )
    return await apps_page(
        request, session,
        notice=tr(f"{name}: {TITLES[action]} gestartet; das dauert ein bis zwei Minuten. "
                  "Danach die Seite neu laden.",
                  f"{name}: {TITLES_EN[action]} started; that takes one to two minutes. "
                  "Then reload the page."),
    )  # fmt: skip


@router.post("/admin/apps/review-account")
async def review_account(
    request: Request,
    session: CurrentSession,
    settings: SettingsDep,
    app: Annotated[str, Form(max_length=32)],
    login: Annotated[str, Form(max_length=254)],
    secret: Annotated[str, Form(max_length=256)],
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    """The demo account a further app's screenshot run signs in with; "Einrichten" puts
    it into the app's Codemagic group <key>_screenshots. Never shown again."""
    admin_only(session, settings)
    other = admin_stores.OTHER_APPS.get(app)
    if other is None or not other.review_vars:
        return await apps_page(
            request,
            session,
            400,
            error=tr("Diese App braucht kein Demo-Konto.", "This app needs no demo account."),
        )
    if not await confirmed(request, session, password):
        return await apps_page(request, session, 400, error=your_password_wrong())
    try:
        value, meta = admin_stores.parse_review(login, secret)
    except admin_stores.StoreError as exc:
        return await apps_page(request, session, 400, error=f"{other.title}: {exc}")
    stores: admin_stores.Stores = request.app.state.stores
    stores.vault.put(other.review, value, meta, session.user.email)
    log.info("demo account for %s screenshots set by %s", other.key, session.user.email)
    return await apps_page(
        request, session,
        notice=f"{other.title}: " + tr(
            "Demo-Konto gespeichert. Jetzt „Einrichten“, dann „Screenshots erzeugen“.",
            'Demo account saved. Now "Set up", then "Make screenshots".'),
    )  # fmt: skip
