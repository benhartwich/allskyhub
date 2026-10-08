"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from allskyhub_server import maintenance
from allskyhub_server.api import app as app_api
from allskyhub_server.api import device as device_api
from allskyhub_server.db import create_engine, create_sessionmaker
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.push.fcm import Sender
from allskyhub_server.push.notify import Notifier
from allskyhub_server.settings import Settings, get_settings
from allskyhub_server.web import (
    routes_account,
    routes_auth,
    routes_cameras,
    routes_gallery,
    routes_legal,
    routes_public,
)
from allskyhub_server.web.deps import LoginRequiredError
from allskyhub_server.web.render import render
from allskyhub_server.web.templating import STATIC_DIR

access_log = logging.getLogger("allskyhub_server.access")

SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"same-origin",
    b"content-security-policy": (
        b"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        b"frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


class AccessLogMiddleware:
    """Logs method, path (never the query string), status and duration; adds security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers.extend((k, v) for k, v in SECURITY_HEADERS.items() if k not in present)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            access_log.info(
                "%s %s %s",
                scope["method"],
                scope["path"],
                status,
                extra={"duration_ms": round((time.perf_counter() - start) * 1000, 1)},
            )


def _wants_html(request: Request) -> bool:
    return not request.url.path.startswith(("/api/", "/device/"))


class CachedStaticFiles(StaticFiles):
    """Pages link static files with ``?v=<content hash>``: those never change."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        versioned = b"v=" in scope.get("query_string", b"")
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if versioned else "no-cache"
        )
        return response


_MESSAGES = {
    403: "Dafür fehlt dir die Berechtigung.",
    404: "Nicht gefunden.",
}


async def _http_error(request: Request, exc: HTTPException) -> Response:
    headers = dict(exc.headers or {})
    if not _wants_html(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)
    message = _MESSAGES.get(exc.status_code, str(exc.detail))
    response = render(
        request, "error.html", {"message": message, "status": exc.status_code},
        status_code=exc.status_code,
    )  # fmt: skip
    response.headers.update(headers)
    return response


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LoginRequiredError)
    async def login_required(request: Request, exc: LoginRequiredError) -> Response:  # pyright: ignore[reportUnusedFunction]
        if not _wants_html(request):
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        target = "/login?next=" + quote(request.url.path, safe="/")
        if request.headers.get("hx-request"):
            return Response(status_code=204, headers={"HX-Redirect": target})
        return RedirectResponse(target, status_code=303)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:  # pyright: ignore[reportUnusedFunction]
        return await _http_error(request, exc)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> Response:  # pyright: ignore[reportUnusedFunction]
        if not _wants_html(request):
            return JSONResponse({"detail": "Invalid request"}, status_code=400)
        return PlainTextResponse("Ungültige Eingabe.", status_code=422)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        cleanup = asyncio.create_task(
            maintenance.run_forever(app.state.sessionmaker, app.state.images, settings)
        )
        sender = (
            Sender(settings.fcm_service_account_file)
            if settings.fcm_service_account_file is not None
            else None
        )
        app.state.notifier = Notifier(app.state.sessionmaker, sender)
        tasks = [cleanup]
        if sender is not None:
            tasks.append(
                asyncio.create_task(app.state.notifier.run_offline_checks(app.state.connections))
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await engine.dispose()

    app = FastAPI(
        title="allskyhub",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.is_dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.is_dev else None,
    )
    app.state.settings = settings
    app.state.connections = ConnectionRegistry()
    app.state.images = ImageStore(settings.image_dir)
    app.add_middleware(AccessLogMiddleware)
    _install_error_handlers(app)

    app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(routes_auth.router)
    app.include_router(routes_account.router)
    app.include_router(routes_cameras.router)
    app.include_router(routes_gallery.router)
    app.include_router(routes_legal.router)
    app.include_router(routes_public.router)
    app.include_router(device_api.router)
    app.include_router(app_api.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        async with app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app
