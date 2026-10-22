"""Template rendering with the common context (user, CSRF token)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import inspect

from allskyhub_server.auth import csrf
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.web import lang
from allskyhub_server.web.templating import templates


def _is_admin(request: Request, session: SessionInfo | None) -> bool:
    """For the menu link only (the admin routes check for themselves). Reads the e-mail
    only if it is loaded: after a rollback the user is expired, and loading it here would
    need the database outside the request's await."""
    admins: frozenset[str] = request.app.state.settings.admins
    if session is None or not admins:
        return False
    email = inspect(session.user).dict.get("email")
    return isinstance(email, str) and email.lower() in admins


def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    *,
    session: SessionInfo | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render for a signed-in user (or anonymously with a double-submit CSRF cookie)."""
    anon_token: str | None = None
    if session is None:
        anon_token = request.cookies.get(csrf.ANON_COOKIE) or csrf.new_anon_token()
    data: dict[str, Any] = {
        "session": session,
        "user": session.user if session else None,
        "csrf_token": session.csrf_token if session else anon_token,
        "mail_enabled": getattr(request.app.state, "mailer", None) is not None
        and request.app.state.mailer.enabled,
        "is_admin": _is_admin(request, session),
    }
    data["en"] = lang.is_en(request)
    data["path_now"] = request.url.path
    data.update(context or {})
    response = templates.TemplateResponse(request, template, data, status_code=status_code)
    if (chosen := lang.asked(request)) is not None:
        lang.remember(request, response, chosen)
    if anon_token and request.cookies.get(csrf.ANON_COOKIE) != anon_token:
        response.set_cookie(
            csrf.ANON_COOKIE,
            anon_token,
            httponly=True,
            secure=request.app.state.settings.session_cookie_secure,
            samesite="strict",
            path="/",
        )
    return response
