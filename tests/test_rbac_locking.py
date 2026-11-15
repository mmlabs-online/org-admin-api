"""Every RBAC writer locks the app row first (ADR-0003 §10, USR-02d carry-over 1)."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.session import SessionLocal
from app.main import create_app
from app.modules.admin_auth.deps import current_admin
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.rbac.models import RoleAssignment
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import RoleCatalogService
from app.modules.users.models import User
from tests.auth_factories import make_login_user
from tests.conftest import TEST_DATABASE_URL, MakeClient
from tests.rbac_factories import (
    assign,
    make_app,
    make_permission,
    make_role,
    make_user,
    super_admin_role,
)
from tests.test_login_concurrency import TIMEOUT, _bounded

BASE = "/api/v1/apps"


@pytest.fixture
async def rbac_client(
    make_client: MakeClient, db_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    actor = await make_user(db_session)  # a real row: `granted_by` references users
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as c:
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
    user = await make_login_user(db_session)  # verified: counted by the guard
    other = await make_login_user(db_session)
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


# --- user status vs revoke: serialized by lock_app("admin") (ADM-002b G5) -------------


async def test_user_status_takes_admin_lock_first(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    with _capture_sql(db_session) as statements:
        resp = await rbac_client.patch(f"/api/v1/users/{user.id}", json={"status": "disabled"})
    assert resp.status_code == 200, resp.text
    app_lock = next(i for i, s in enumerate(statements) if _is_app_lock(s))
    user_lock = next(
        i for i, s in enumerate(statements) if "FROM users" in s and "FOR NO KEY UPDATE" in s
    )
    assert app_lock < user_lock, statements


async def _committed_super_admins() -> tuple[User, User]:
    async with SessionLocal() as session:
        first = await make_login_user(session)
        second = await make_login_user(session)
        role = await super_admin_role(session)
        await assign(session, first, role)
        await assign(session, second, role)
        await session.commit()
    return first, second


async def _cleanup(*users: User) -> None:
    ids = [u.id for u in users]
    async with SessionLocal() as session:
        await session.execute(delete(RoleAssignment).where(RoleAssignment.user_id.in_(ids)))
        await session.execute(delete(User).where(User.id.in_(ids)))
        await session.commit()


async def _request_as(actor: User, method: str, url: str, body: dict[str, Any] | None) -> Response:
    """A request with the real `get_session` (own connection, commit at the end)."""
    app = create_app()
    principal = AdminPrincipal(actor.id, frozenset({"admin:*"}), "race")
    app.dependency_overrides[current_admin] = lambda: principal
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, json=body)


async def _wait_for_lock_waiters(count: int) -> None:
    while True:
        async with SessionLocal() as probe:
            waiting = await probe.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        if (waiting or 0) >= count:
            return
        await asyncio.sleep(0.02)


async def _race(*requests: tuple[User, str, str, dict[str, Any] | None]) -> list[Response]:
    """Hold the admin lock until every request waits on it, then let them run one by one."""
    async with SessionLocal() as blocker:
        await blocker.execute(text("SELECT 1 FROM apps WHERE key = 'admin' FOR NO KEY UPDATE"))
        tasks = [asyncio.create_task(_request_as(*r)) for r in requests]
        try:
            await _bounded(_wait_for_lock_waiters(len(requests)))
            assert not any(t.done() for t in tasks)
        finally:
            await blocker.commit()
            results = await asyncio.wait_for(asyncio.gather(*tasks), TIMEOUT)
    return list(results)


async def _active_holders(*users: User) -> list[uuid.UUID]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(User.id)
            .join(RoleAssignment, RoleAssignment.user_id == User.id)
            .where(User.id.in_([u.id for u in users]), User.status == "active")
        )
        return list(rows)


def _one_refused(responses: list[Response]) -> None:
    codes = sorted(r.status_code for r in responses)
    assert codes[1] == 409, [r.text for r in responses]
    assert codes[0] in {200, 204}, [r.text for r in responses]
    refused = next(r for r in responses if r.status_code == 409)
    assert refused.json()["error"]["code"] == "last_super_admin"


async def test_concurrent_disable_pair_serialized() -> None:
    first, second = await _committed_super_admins()
    try:
        responses = await _race(
            (first, "PATCH", f"/api/v1/users/{second.id}", {"status": "disabled"}),
            (second, "PATCH", f"/api/v1/users/{first.id}", {"status": "disabled"}),
        )
        _one_refused(responses)
        assert len(await _active_holders(first, second)) == 1
    finally:
        await _cleanup(first, second)


async def test_concurrent_revoke_and_disable_serialized() -> None:
    first, second = await _committed_super_admins()
    async with SessionLocal() as session:
        first_ra = await session.scalar(
            select(RoleAssignment.id).where(RoleAssignment.user_id == first.id)
        )
    try:
        responses = await _race(
            (second, "DELETE", f"/api/v1/users/{first.id}/role-assignments/{first_ra}", None),
            (first, "PATCH", f"/api/v1/users/{second.id}", {"status": "disabled"}),
        )
        _one_refused(responses)
        assert len(await _active_holders(first, second)) == 1
    finally:
        await _cleanup(first, second)
