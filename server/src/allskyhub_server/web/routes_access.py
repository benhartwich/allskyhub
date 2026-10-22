"""Asking for access (/zugang), the parts list (/hardware), the first steps (/start) and
the guide for cameras that already run Allsky (/allsky).

There is no open sign-up (auth/invitations.py): a request reaches the admins, who invite or
decline on the admin page. ``/start`` walks a new account from buying parts to the paired
camera; the home page points there while the account has no camera.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from allskyhub_server.auth import access_requests, ratelimit
from allskyhub_server.auth.accounts import AccountError
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.web import hardware
from allskyhub_server.web.deps import (
    CurrentSession,
    DbSession,
    OptionalSession,
    SettingsDep,
    client_ip,
    csrf_protect,
)
from allskyhub_server.web.image_download import LatestImage
from allskyhub_server.web.lang import is_en
from allskyhub_server.web.render import render

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(csrf_protect)])

STEPS = ("hardware", "zusammenbau", "image", "koppeln")


def _hardware_context() -> dict[str, object]:
    return {
        "groups": hardware.GROUPS,
        "shop": hardware.SHOP,
        "housing": hardware.HOUSING,
        "videos": hardware.VIDEOS,
        "total_hq": hardware.TOTAL_HQ,
        "total_zwo": hardware.TOTAL_ZWO,
        "voucher": hardware.VOUCHER,
        "voucher_from": hardware.VOUCHER_FROM,
        "voucher_off": hardware.VOUCHER_OFF,
    }


def _stages(en: bool) -> dict[str, str]:
    return access_requests.STAGES_EN if en else access_requests.STAGES


@router.get("/zugang")
async def access_form(request: Request, session: OptionalSession) -> Response:
    """German or English (web/lang.py)."""
    stages = _stages(is_en(request))
    return render(request, "zugang.html", {"stages": stages}, session=session)


@router.post("/zugang")
async def access_request(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    session: OptionalSession,
    email: Annotated[str, Form(max_length=254)],
    name: Annotated[str, Form(max_length=100)],
    stage: Annotated[str, Form(max_length=16)],
    place: Annotated[str, Form(max_length=100)] = "",
    message: Annotated[str, Form(max_length=1000)] = "",
    privacy: Annotated[bool, Form()] = False,
    website: Annotated[str, Form(max_length=200)] = "",  # left empty by people
    lang: Annotated[str, Form(max_length=2)] = "",
) -> Response:
    en = lang == "en" if lang else is_en(request)
    form = {"email": email, "name": name, "stage": stage, "place": place, "message": message}
    context: dict[str, object] = {"stages": _stages(en), "form": form, "en": en}
    if not privacy:
        context["error"] = (
            "Please confirm that you have read the privacy policy."
            if en
            else "Bitte bestätige, dass du die Datenschutzerklärung gelesen hast."
        )
        return render(request, "zugang.html", context, session=session, status_code=400)
    if website:
        # A bot filled the hidden field: same answer as for a real request.
        return render(request, "zugang.html", {"sent": True, "en": en}, session=session)
    try:
        await ratelimit.hit(
            request.app.state.engine,
            f"access:ip:{client_ip(request, settings)}",
            ratelimit.ACCESS_PER_IP,
        )
        await ratelimit.hit(request.app.state.engine, "access:all", ratelimit.ACCESS_ALL)
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Zu viele Anfragen. Bitte später erneut versuchen.",
            {"Retry-After": str(exc.retry_after)},
        ) from None  # fmt: skip
    try:
        created = await access_requests.create(db, email, name, place, stage, message)
    except AccountError as exc:
        context["error"] = exc.message
        return render(request, "zugang.html", context, session=session, status_code=400)
    await db.commit()
    mailer: Mailer = request.app.state.mailer
    if created is not None and mailer.enabled:
        admin = f"{settings.base_url.rstrip('/')}/admin#zugang"
        for to in sorted(settings.admins):
            await mailer.send(
                Mail(
                    to=to,
                    subject=f"allskyhub: Zugangsanfrage von {created.name}",
                    body=(
                        f"{created.name} <{created.email}> fragt nach einem Zugang"
                        f"{' (Englisch)' if en else ''}.\n\n"
                        f"Stand: {access_requests.STAGES[created.stage]}\n"
                        f"Ort: {created.place or '–'}\n\n{created.message or ''}\n\n"
                        f"Einladen oder ablehnen: {admin}\n"
                    ),
                )
            )
    return render(request, "zugang.html", {"sent": True, "en": en}, session=session)


@router.get("/hardware")
async def hardware_page(request: Request, session: OptionalSession) -> Response:
    return render(request, "hardware.html", _hardware_context(), session=session)


@router.get("/allsky")
async def allsky_guide(request: Request, session: OptionalSession) -> Response:
    """allskyhub next to an existing Allsky (docs/install.md 2.6, 2.7), German or English
    (web/lang.py)."""
    return render(request, "allsky_guide.html", {}, session=session)


@router.get("/download/image")
async def download_image(request: Request) -> Response:
    """The newest SD card image (the latest release on GitHub), for links in posts."""
    latest: LatestImage = request.app.state.latest_image
    image = await latest.get()
    return RedirectResponse(image.url if image else latest.releases_url, status_code=302)


@router.get("/start")
async def first_steps(
    request: Request, session: CurrentSession, schritt: str = STEPS[0]
) -> Response:
    if schritt not in STEPS:
        raise HTTPException(404)
    index = STEPS.index(schritt)
    latest: LatestImage = request.app.state.latest_image
    return render(
        request,
        "start.html",
        {
            **_hardware_context(),
            "image": await latest.get() if schritt == "image" else None,
            "step": schritt,
            "index": index,
            "steps": STEPS,
            "prev": STEPS[index - 1] if index > 0 else None,
            "next": STEPS[index + 1] if index + 1 < len(STEPS) else None,
        },
        session=session,
    )
