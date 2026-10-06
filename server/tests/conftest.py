"""Shared fixtures: a migrated PostgreSQL test database and an app client.

Needs ALLSKYHUB_SERVER_TEST_DATABASE_URL (``tools/dev-postgres.sh``, then ``source .dev/env``).
Without it the hub tests are skipped, so agent work on a Pi does not need PostgreSQL.
The schema ``public`` of that database is dropped on every run: never point it at real data.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from allskyhub_server.app import create_app
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
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
