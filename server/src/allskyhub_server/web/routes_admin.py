"""Admin page: the server's state and deploying new versions (admin/deploy.py).

Only for accounts in ``admin_emails``; everyone else gets a 404, as if the page did not
exist. Starting a deploy needs the password again.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from allskyhub_server.admin import deploy
from allskyhub_server.auth import ratelimit
from allskyhub_server.auth.passwords import verify_secret_async
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.models import Device, User
from allskyhub_server.settings import Settings
from allskyhub_server.version import full as running_version
from allskyhub_server.web.deps import CurrentSession, DbSession, SettingsDep, csrf_protect
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])


def _admin(session: SessionInfo, settings: Settings) -> None:
    if session.user.email.lower() not in settings.admins:
        raise HTTPException(404)


async def _page(
    request: Request,
    db: DbSession,
    session: SessionInfo,
    status_code: int = 200,
    **extra: object,
) -> Response:
    settings: Settings = request.app.state.settings
    github: deploy.GitHub = request.app.state.github
    running = running_version()
    upstream: deploy.Upstream | None = None
    upstream_error: str | None = None
    try:
        upstream = await github.upstream(running)
    except deploy.DeployError as exc:
        upstream_error = str(exc)
    disk = await asyncio.to_thread(shutil.disk_usage, settings.data_dir)
    return render(
        request,
        "admin.html",
        {
            "running": running,
            "upstream": upstream,
            "upstream_error": upstream_error,
            "job": deploy.job(settings.data_dir),
            "disk_free_gb": disk.free / 1e9,
            "disk_free_pct": 100 * disk.free / disk.total,
            "users": await db.scalar(select(func.count()).select_from(User)),
            "cameras": await db.scalar(
                select(func.count()).select_from(Device).where(Device.owner_id.is_not(None))
            ),
            "repo": settings.deploy_repo,
            **extra,
        },
        session=session,
        status_code=status_code,
    )


@router.get("/admin")
async def admin_page(
    request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep
) -> Response:
    _admin(session, settings)
    return await _page(request, db, session)


@router.get("/admin/deploy/job")
async def deploy_job(request: Request, session: CurrentSession, settings: SettingsDep) -> Response:
    """The helper's progress, polled by the page while a deploy runs."""
    _admin(session, settings)
    return render(
        request,
        "_deploy_job.html",
        {"job": deploy.job(settings.data_dir), "running": running_version()},
        session=session,
    )


@router.post("/admin/deploy")
async def start_deploy(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    sha: Annotated[str, Form(max_length=40)],
    password: Annotated[str, Form(max_length=1024)],
) -> Response:
    _admin(session, settings)
    try:
        await ratelimit.hit(
            request.app.state.engine, f"account:{session.user.id}", ratelimit.LOGIN_PER_ACCOUNT
        )
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Zu viele Versuche.", {"Retry-After": str(exc.retry_after)}
        ) from None
    if not await verify_secret_async(session.user.password_hash, password):
        return await _page(request, db, session, 400, error="Das Passwort stimmt nicht.")
    github: deploy.GitHub = request.app.state.github
    github.forget()  # decide on GitHub's current answer, not a cached one
    try:
        upstream = await github.upstream(running_version())
        deploy.request(settings.data_dir, sha, session.user.email, running_version(), upstream)
    except deploy.DeployError as exc:
        return await _page(request, db, session, 400, error=str(exc))
    return await _page(
        request, db, session, notice="Deploy angefordert. Der Server holt ihn gleich ab."
    )


@router.post("/admin/mail-test")
async def mail_test(
    request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep
) -> Response:
    _admin(session, settings)
    mailer: Mailer = request.app.state.mailer
    if not mailer.enabled:
        return await _page(request, db, session, 400, error="E-Mail ist nicht eingerichtet.")
    sent = await mailer.send(
        Mail(
            to=session.user.email,
            subject="allskyhub: Testmail",
            body="Diese Testmail kam von der Admin-Seite. Der Versand funktioniert.\n",
        )
    )
    if not sent:
        return await _page(
            request, db, session, 502, error="Der Mailserver hat die Mail nicht angenommen."
        )
    return await _page(request, db, session, notice=f"Testmail an {session.user.email} ist raus.")
