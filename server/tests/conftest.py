"""Shared fixtures: a migrated PostgreSQL test database and an app client.

Needs ALLSKYHUB_SERVER_TEST_DATABASE_URL (``tools/dev-postgres.sh``, then ``source .dev/env``).
Without it the hub tests are skipped, so agent work on a Pi does not need PostgreSQL.
The schema ``public`` of that database is dropped on every run: never point it at real data.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from allskyhub_server.app import create_app
from allskyhub_server.devices.satellites import TleCache
from allskyhub_server.devices.space_weather import KpCache, OvationCache
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.models import Base
from allskyhub_server.settings import Settings

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
TEST_DB_URL = os.environ.get("ALLSKYHUB_SERVER_TEST_DATABASE_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DB_URL:
        return
    skip = pytest.mark.skip(reason="ALLSKYHUB_SERVER_TEST_DATABASE_URL is not set")
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    assert TEST_DB_URL
    return Settings(
        env="dev",
        database_url=TEST_DB_URL,
        base_url="http://testserver",
        data_dir=tmp_path_factory.mktemp("data"),
        session_cookie_secure=False,
        log_format="console",
    )


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """Fresh schema per test session, migrated to head via Alembic."""
    await _reset_schema(settings.async_database_url)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["database_url"] = settings.async_database_url
    # env.py runs its own event loop; keep it off this one.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    eng = create_async_engine(settings.async_database_url)
    yield eng
    await eng.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Truncate all application tables after each test that touched the database."""
    yield
    if "engine" not in request.fixturenames:
        return
    eng: AsyncEngine = request.getfixturevalue("engine")
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with eng.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def app(settings: Settings, engine: AsyncEngine) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    # No requests to NOAA from tests: a fixed Kp forecast for today and tomorrow.
    application.state.kp = KpCache(httpx.AsyncClient(transport=httpx.MockTransport(_kp_answer)))
    application.state.ovation = OvationCache(
        httpx.AsyncClient(transport=httpx.MockTransport(_ovation_answer))
    )
    # Nor to CelesTrak: a fixed ISS element set (test_satellites.py).
    application.state.tles = TleCache(httpx.AsyncClient(transport=httpx.MockTransport(_tle_answer)))
    # Nor to Open-Meteo: clear skies everywhere.
    application.state.forecast = ForecastCache(
        httpx.AsyncClient(transport=httpx.MockTransport(_forecast_answer))
    )
    async with application.router.lifespan_context(application):
        yield application


OVATION_JPEG = b"\xff\xd8\xff\xe0ovation"


def _ovation_answer(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=OVATION_JPEG)


def _kp_answer(request: httpx.Request) -> httpx.Response:
    today = dt.datetime.now(dt.UTC).date()
    rows = [
        {"time_tag": f"{today}T00:00:00", "kp": 2.33, "observed": "observed"},
        {"time_tag": f"{today}T03:00:00", "kp": 4.67, "observed": "estimated"},
        {
            "time_tag": f"{today + dt.timedelta(days=1)}T00:00:00",
            "kp": 5.33,
            "observed": "predicted",
        },
    ]
    return httpx.Response(200, json=rows)


ISS_TLE = (
    "1 25544U 98067A   26285.14422154  .00005771  00000+0  11345-3 0  9992",
    "2 25544  51.6314  81.3064 0006731 252.4466 107.5787 15.48833724589888",
)


def _tle_answer(request: httpx.Request) -> httpx.Response:
    if request.url.params.get("CATNR") != "25544":
        return httpx.Response(404)
    return httpx.Response(200, text="ISS (ZARYA)\n" + "\n".join(ISS_TLE) + "\n")


def _forecast_answer(request: httpx.Request) -> httpx.Response:
    start = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    times = [int((start + dt.timedelta(hours=i)).timestamp()) for i in range(72)]
    return httpx.Response(200, json={"hourly": {"time": times, "cloud_cover": [5] * 72}})


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def new_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """A second browser (no shared cookies) on the same app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def live_app(app: FastAPI) -> AsyncIterator[str]:
    """``app`` served by uvicorn on localhost, for WebSocket tests; yields host:port."""
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
