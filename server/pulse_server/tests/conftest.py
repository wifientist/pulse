"""Shared test fixtures: a disposable SQLite file, a FastAPI app wired to it, and an
httpx AsyncClient that speaks to the app in-process.

We create tables via `Base.metadata.create_all` rather than running alembic — migrations
are validated separately in scaffold-verification. This keeps each test's setup fast.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from pulse_server.config import Settings
from pulse_server.db.engine import build_engine, build_sessionmaker
from pulse_server.db.models import Base
from pulse_server.main import create_app

ADMIN_TOKEN = "test-admin-token"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    db_path = tmp_path / "pulse-test.sqlite"
    return Settings(
        admin_token=ADMIN_TOKEN,
        db_path=str(db_path),
        log_level="WARNING",
        default_poll_interval_s=1,
        default_ping_interval_s=1,
    )


@pytest_asyncio.fixture
async def app(settings: Settings) -> AsyncIterator:
    engine = build_engine(settings.db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    _app = create_app(settings)
    async with _app.router.lifespan_context(_app):
        yield _app


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://pulse.test") as c:
        yield c


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}
