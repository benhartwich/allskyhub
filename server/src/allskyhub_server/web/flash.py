"""Post/Redirect/Get for the admin pages (Benjamin, 2026-10-29: reloading a page after a
button ran the action again).

A POST that ends on an admin page leaves what the page would show (notice, error,
report, status) here and answers 303 to the page with ``?m=<token>``; the GET shows it
once. Kept in memory: the hub runs one worker, and a flash lost in a restart only hides a
message, never the state the page reads itself.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from allskyhub_server.auth.sessions import SessionInfo

TTL_S = 600.0
MAX = 200  # flashes kept at most, the oldest go first


class Flashes:
    def __init__(self) -> None:
        self._kept: dict[str, tuple[float, str, dict[str, Any]]] = {}

    def keep(self, session: SessionInfo, data: dict[str, Any]) -> str:
        now = time.monotonic()
        self._kept = {k: v for k, v in self._kept.items() if v[0] > now}
        while len(self._kept) >= MAX:
            del self._kept[next(iter(self._kept))]
        token = secrets.token_urlsafe(12)
        self._kept[token] = (now + TTL_S, str(session.session_id), data)
        return token

    def take(self, session: SessionInfo, token: str) -> dict[str, Any]:
        """The flash once, for the session that left it; else nothing."""
        kept = self._kept.get(token)
        if kept is None or kept[1] != str(session.session_id):
            return {}
        del self._kept[token]
        return kept[2] if kept[0] > time.monotonic() else {}


def _flashes(request: Request) -> Flashes:
    found: Flashes | None = getattr(request.app.state, "flashes", None)
    if found is None:
        found = request.app.state.flashes = Flashes()
    return found


def redirect(
    request: Request, session: SessionInfo, url: str, status_code: int, extra: dict[str, Any]
) -> Response:
    """After a POST: keep what the page would show, send the browser to the page."""
    token = _flashes(request).keep(session, {**extra, "status_code": status_code})
    return RedirectResponse(f"{url}?m={token}", status_code=303)


def take(request: Request, session: SessionInfo) -> tuple[int, dict[str, Any]]:
    """On the GET: the status and what the POST left, once (200 and nothing otherwise)."""
    token = request.query_params.get("m", "")
    data = dict(_flashes(request).take(session, token)) if token else {}
    return int(data.pop("status_code", 200)), data
