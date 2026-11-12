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
from tests.rbac_factories import (
    assign,
    make_app,
    make_permission,
    make_role,
    make_user,
    super_admin_role,
)

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


# --- role assignments (USR-02c) ------------------------------------------------


def _writes_assignments(statement: str) -> bool:
    upper = statement.lstrip().upper()
    return upper.startswith(("INSERT", "UPDATE", "DELETE")) and "ROLE_ASSIGNMENTS" in upper


def _is_guard_count(statement: str) -> bool:
    return "count(" in statement and "role_assignments" in statement


def _assert_locked_first(statements: list[str]) -> None:
    lock = next(i for i, s in enumerate(statements) if _is_app_lock(s))
    later = [i for i, s in enumerate(statements) if _writes_assignments(s) or _is_guard_count(s)]
    assert later, statements
    assert all(lock < i for i in later), statements


async def test_assignment_writes_lock_role_app_first(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    other = await make_user(db_session)
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:viewer")
    super_admin = await super_admin_role(db_session)
    url = f"/api/v1/users/{user.id}/role-assignments"

    with _capture_sql(db_session) as statements:
        resp = await rbac_client.post(url, json={"role_id": str(role.id)})
    assert resp.status_code == 201, resp.text
    _assert_locked_first(statements)

    with _capture_sql(db_session) as statements:
        resp = await rbac_client.delete(f"{url}/{resp.json()['id']}")
    assert resp.status_code == 204, resp.text
    _assert_locked_first(statements)

    # Admin role: the guard count runs after the `admin` lock too.
    await assign(db_session, other, super_admin)
    target = await assign(db_session, user, super_admin)
    with _capture_sql(db_session) as statements:
        resp = await rbac_client.delete(f"{url}/{target.id}")
    assert resp.status_code == 204, resp.text
    _assert_locked_first(statements)
    assert any(_is_guard_count(s) for s in statements)


async def test_super_admin_revoke_holds_admin_lock(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    other = await make_user(db_session)
    super_admin = await super_admin_role(db_session)
    await assign(db_session, other, super_admin)
    target = await assign(db_session, user, super_admin)

    resp = await rbac_client.delete(f"/api/v1/users/{user.id}/role-assignments/{target.id}")
    assert resp.status_code == 204, resp.text

    # The request's transaction is still open: a concurrent revoke would wait for it.
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as exc_info:
                await conn.execute(
                    text("SELECT 1 FROM apps WHERE key = 'admin' FOR NO KEY UPDATE NOWAIT")
                )
            assert getattr(exc_info.value.orig, "sqlstate", None) == "55P03"
            await conn.rollback()
    finally:
        await engine.dispose()
