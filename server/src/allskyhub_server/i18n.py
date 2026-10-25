"""The language of the hub's answers to the app (Benjamin, 2026-10-25).

The app sends ``Accept-Language``; for ``/api/`` requests :class:`LanguageMiddleware` picks
German or English for the rest of the request, and the texts the API returns read it with
``tr("Deutsch", "English")``, German first like in the app (app/lib/l10n.dart). Outside an
API request (web pages, mails, push, background jobs) it is German, as before.
"""

from __future__ import annotations

from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

_english: ContextVar[bool] = ContextVar("english", default=False)


def english() -> bool:
    return _english.get()


def tr(de: str, en: str) -> str:
    """The text in the language of the current API request."""
    return en if _english.get() else de


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


class LanguageMiddleware:
    """Sets the language for requests to the app API (``/api/``)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith("/api/"):
            await self.app(scope, receive, send)
            return
        header = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"accept-language"),
            None,
        )
        token = _english.set(prefers_english(header))
        try:
            await self.app(scope, receive, send)
        finally:
            _english.reset(token)
