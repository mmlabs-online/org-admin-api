import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

# Point the app at the test database before anything imports app settings.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/org_admin_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["APP_ENV"] = "test"
# Cheap Argon2id parameters so tests stay fast.
os.environ["ARGON2_MEMORY_KIB"] = "1024"
os.environ["ARGON2_TIME_COST"] = "1"
os.environ["ARGON2_PARALLELISM"] = "1"
# Test-only HMAC key for login failure counters (required in staging/production).
TEST_IDENTITY_HMAC_KEY = "test-identity-hmac-key-0123456789abcdef"
os.environ["IDENTITY_HMAC_KEY"] = TEST_IDENTITY_HMAC_KEY

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.modules.auth.deps import get_turnstile_verifier
from tests.fakes import FakeTurnstileVerifier

MakeClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]


@pytest.fixture(scope="session", autouse=True)
def _migrate_db() -> None:
    """Build the schema with the real migrations (downgrade first => clean slate)."""
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Each test runs inside a transaction that is rolled back afterwards."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


@pytest.fixture
def fake_turnstile() -> FakeTurnstileVerifier:
    return FakeTurnstileVerifier()


@pytest.fixture
def make_client(db_session: AsyncSession, fake_turnstile: FakeTurnstileVerifier) -> MakeClient:
    """Build a client for an app with optional settings overrides (same DB session)."""

    @asynccontextmanager
    async def _make(**settings_overrides: Any) -> AsyncIterator[AsyncClient]:
        app = create_app(Settings(**settings_overrides) if settings_overrides else None)

        async def _override_session() -> AsyncIterator[AsyncSession]:
            yield db_session

        app.dependency_overrides[get_session] = _override_session
        app.dependency_overrides[get_turnstile_verifier] = lambda: fake_turnstile
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    return _make


MakeStrictApp = Callable[..., FastAPI]


@pytest.fixture
def make_strict_app(
    db_session: AsyncSession, fake_turnstile: FakeTurnstileVerifier
) -> MakeStrictApp:
    """The app behind `make_strict_client`: the session mirrors `get_session` (commit on
    success, rollback on exception)."""

    def _make(**settings_overrides: Any) -> FastAPI:
        app = create_app(Settings(**settings_overrides) if settings_overrides else None)

        async def _override_session() -> AsyncIterator[AsyncSession]:
            try:
                yield db_session
                await db_session.commit()
            except Exception:
                await db_session.rollback()
                raise

        app.dependency_overrides[get_session] = _override_session
        app.dependency_overrides[get_turnstile_verifier] = lambda: fake_turnstile
        return app

    return _make


@pytest.fixture
def make_strict_client(make_strict_app: MakeStrictApp) -> MakeClient:
    """Like `make_client`, but the session mirrors `get_session`: commit on success, rollback
    on exception. Commit test setup (`await db_session.commit()`, releases the savepoint; the
    outer test transaction still rolls back) before calling it."""

    @asynccontextmanager
    async def _make(**settings_overrides: Any) -> AsyncIterator[AsyncClient]:
        app = make_strict_app(**settings_overrides)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    return _make


@pytest.fixture
async def client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client() as c:
        yield c
