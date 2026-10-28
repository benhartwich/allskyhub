"""Admin: the apps and their stores on one page (admin/apps.py), each with its next step.

The accounts themselves (keys, tokens) stay on the admin page; this page only uses them.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from allskyhub_server.admin import apps, appstore
from allskyhub_server.admin import stores as admin_stores
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.web.deps import CurrentSession, SettingsDep, csrf_protect
from allskyhub_server.web.render import render
from allskyhub_server.web.routes_admin import admin_only

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
}


async def apps_page(
    request: Request,
    session: SessionInfo,
    status_code: int = 200,
    *,
    refresh: bool = False,
    **extra: object,
) -> Response:
    stores: admin_stores.Stores = request.app.state.stores
    cards = await apps.cards(stores)
    return render(
        request,
        "admin_apps.html",
        {
            "cards": cards,
            "titles": TITLES,
            "android_busy": stores.android_busy(),
            # Reload by itself while something builds, only on a GET (never re-posts).
            "refresh": refresh and any(c.waiting for c in cards),
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
    return await apps_page(request, session, refresh=True)


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
        return await apps_page(request, session, 400, error="Unbekannte App oder Aktion.")
    stores: admin_stores.Stores = request.app.state.stores
    by = session.user.email
    name = other.title if other else "allskyhub"
    try:
        if action == "setup":
            report = await (stores.setup_other(other, by) if other else stores.setup(by))
            return await apps_page(
                request, session, report=report, report_title=f"{name}: Einrichten"
            )
        if action in ("build-android", "build-ios"):
            platform = action.removeprefix("build-")
            await stores.build(platform, by, app=other)
            notice = (
                "Der Server baut das App Bundle (gut 10 Minuten) und lädt es in den internen Test."
                if platform == "android"
                else "Codemagic baut (etwa 20 Minuten) und schickt den Build nach TestFlight."
            )
            return await apps_page(request, session, notice=f"{name}: {notice}")
        if action == "play-listing":
            started = stores.start_listing(by, other)
        elif action == "appstore":
            started = appstore.start(stores, by, other)
        else:
            store = action.removeprefix("review-")
            if store == "play":
                report = await stores.submit_play(other.package if other else admin_stores.PACKAGE)
            else:
                report = await appstore.submit_report(stores, other)
            apps.save_review(stores, other.key if other else "allskyhub", store, by, report)
            log.info(
                "review %s %s submitted by %s: %s", store, name, by,
                [(x.title, x.state, x.text) for x in report.steps],
            )  # fmt: skip
            return await apps_page(
                request, session, report=report, report_title=f"{name}: {TITLES[action]}"
            )
    except admin_stores.StoreError as exc:
        return await apps_page(request, session, 400, error=f"{name}: {exc}")
    if not started:
        return await apps_page(request, session, 409, error=f"{name}: Eine Übertragung läuft noch.")
    return await apps_page(
        request, session,
        notice=f"{name}: {TITLES[action]} gestartet; das dauert ein bis zwei Minuten. "
        "Danach die Seite neu laden.",
    )  # fmt: skip
