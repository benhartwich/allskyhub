"""The language of the hub's answers (Benjamin, 2026-10-25; web pages 2026-11-05).

The app sends ``Accept-Language``; for ``/api/`` requests :class:`LanguageMiddleware` picks
German or English for the rest of the request, and the texts the API returns read it with
``tr("Deutsch", "English")``, German first like in the app (app/lib/l10n.dart). A web page
takes the language its visitor chose (``?lang=`` or the cookie, web/lang.py), so messages
of the pages that have both languages (SharedSky, sign-up) follow it too. Mails to a user
outside a request use the user's stored language (``pick``); everything else is German.
"""

from __future__ import annotations

from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

_english: ContextVar[bool] = ContextVar("english", default=False)


def english() -> bool:
    return _english.get()


def tr(de: str, en: str) -> str:
    """The text in the language of the current request."""
    return en if _english.get() else de


def pick(lang: str | None, de: str, en: str) -> str:
    """The text in a stored language (``User.lang``), for mails outside a request."""
    return en if lang == "en" else de


def prefers_english(header: str | None) -> bool:
    """Whether ``Accept-Language`` asks for English before German. Loose on purpose
    ("en-GB", q-values, "*"): the highest-ranked of the two decides, German on a tie or
    when neither is named."""
    best: dict[str, float] = {}
    for part in (header or "").split(","):
        tag, _, params = part.strip().partition(";")
        lang = tag.strip().lower().split("-")[0]
        if lang not in ("de", "en"):
            continue
        q = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.strip() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        best[lang] = max(best.get(lang, 0.0), q)
    return best.get("en", 0.0) > best.get("de", 0.0)


def page_english(query: str, cookie_header: str | None) -> bool:
    """A web page's language as web/lang.py chooses it: ``?lang=`` first, then the
    ``ash_lang`` cookie; German otherwise."""
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == "lang" and value in ("de", "en"):
            return value == "en"
    for part in (cookie_header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == "ash_lang":
            return value.strip() == "en"
    return False


class LanguageMiddleware:
    """Sets the language of a request: for the app API (``/api/``) from
    ``Accept-Language``, for web pages from ``?lang=`` or the cookie."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])

        def header(name: bytes) -> str | None:
            return next((v.decode("latin-1") for k, v in headers if k == name), None)

        if str(scope.get("path", "")).startswith("/api/"):
            english_now = prefers_english(header(b"accept-language"))
        else:
            query = bytes(scope.get("query_string", b"")).decode("latin-1")
            english_now = page_english(query, header(b"cookie"))
        token = _english.set(english_now)
        try:
            await self.app(scope, receive, send)
        finally:
            _english.reset(token)
