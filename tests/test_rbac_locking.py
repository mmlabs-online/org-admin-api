"""Every RBAC writer locks the app row first (ADR-0003 §10, USR-02d carry-over 1)."""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import RoleCatalogService
from tests.conftest import TEST_DATABASE_URL, MakeClient
from tests.rbac_factories import make_app, make_permission, make_role

BASE = "/api/v1/apps"


@pytest.fixture
async def rbac_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_rbac_api_enabled=True) as c:
        yield c


@contextmanager
def _capture_sql(db_session: AsyncSession) -> Iterator[list[str]]:
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(conn, "before_cursor_execute", _record)


def _is_app_lock(statement: str) -> bool:
    return "FROM apps" in statement and "FOR NO KEY UPDATE" in statement


async def test_lock_app_blocks_lockers_not_key_share(db_session: AsyncSession) -> None:
    assert await RbacRepository(db_session).lock_app("admin") is not None  # seeded, committed

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as other:
            with pytest.raises(DBAPIError) as exc_info:
                await other.execute(
                    text("SELECT 1 FROM apps WHERE key = 'admin' FOR NO KEY UPDATE NOWAIT")
                )
            assert getattr(exc_info.value.orig, "sqlstate", None) == "55P03"  # lock_not_available
            await other.rollback()

            # FK checks take KEY SHARE: not blocked by the app lock.
            result = await other.execute(
                text("SELECT 1 FROM apps WHERE key = 'admin' FOR KEY SHARE NOWAIT")
            )
            assert result.scalar() == 1
            await other.rollback()
    finally:
        await engine.dispose()


async def _setup(db_session: AsyncSession) -> dict[str, uuid.UUID]:
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:read")
    role = await make_role(db_session, app, "stoktok:viewer")
    return {"perm": perm.id, "role": role.id}


_MUTATIONS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/stoktok/permissions", {"key": "stoktok:b:read"}),
    ("PATCH", "/stoktok/permissions/{perm}", {"description": "d"}),
    ("DELETE", "/stoktok/permissions/{perm}", None),
    ("POST", "/stoktok/roles", {"key": "stoktok:editor", "name": "E"}),
    ("PATCH", "/stoktok/roles/{role}", {"name": "V"}),
    ("DELETE", "/stoktok/roles/{role}", None),
    ("PUT", "/stoktok/roles/{role}/permissions", {"permission_keys": ["stoktok:a:read"]}),
    ("PATCH", "/stoktok", {"name": "S2"}),
    ("DELETE", "/stoktok", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _MUTATIONS)
async def test_mutations_lock_app_first(
    rbac_client: AsyncClient,
    db_session: AsyncSession,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    ids = await _setup(db_session)
    with _capture_sql(db_session) as statements:
        resp = await rbac_client.request(method, BASE + path.format(**ids), json=body)
    assert resp.status_code < 300, resp.text
    first = statements[0]
    assert _is_app_lock(first), first


async def test_build_default_role_locks_app(db_session: AsyncSession) -> None:
    await _setup(db_session)
    with _capture_sql(db_session) as statements:
        await RoleCatalogService(db_session).build_default_role("stoktok")
    assert statements and _is_app_lock(statements[0]), statements[:1]
