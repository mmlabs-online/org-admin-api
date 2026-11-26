import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page
from app.modules.users.models import User
from app.modules.users.schemas import UserRead
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_app, make_org, make_role

BASE = "/api/v1/users"
USER_READ_KEYS = set(UserRead.model_fields)
DAY = timedelta(days=1)


@pytest.fixture
async def users_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"]) as c:
        yield c


class Env:
    """A unique email fragment so list tests see only their own users."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.tag = uuid.uuid4().hex[:12]
        self.n = 0

    async def user(self) -> User:
        self.n += 1
        user = User(email=f"u{self.n}-{self.tag}@acc.test", password_hash="h")
        self.db.add(user)
        await self.db.flush()
        return user

    async def list(self, client: AsyncClient, query: str = "include=access") -> dict[str, Any]:
        resp = await client.get(f"{BASE}?email={self.tag}&{query}")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    def by_id(self, body: dict[str, Any], user: User) -> dict[str, Any]:
        return next(i for i in body["items"] if i["id"] == str(user.id))


@pytest.fixture
def env(db_session: AsyncSession) -> Env:
    return Env(db_session)


def _flat(item: dict[str, Any]) -> list[tuple[str, str, bool]]:
    return [
        (g["app"], r["role_key"], r["org_id"] is None) for g in item["access"] for r in g["roles"]
    ]


def _iso(dt: datetime) -> datetime:
    return dt


async def test_include_access_shape(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    user = await env.user()
    app = await make_app(db_session, "alpha")
    role = await make_role(db_session, app, "alpha:viewer")
    exp = datetime.now(UTC) + 30 * DAY
    ra = await assign(db_session, user, role, expires_at=exp)
    item = env.by_id(await env.list(users_client), user)
    assert set(item) == USER_READ_KEYS | {"access", "access_truncated"}
    assert item["access_truncated"] is False
    assert len(item["access"]) == 1
    group = item["access"][0]
    assert set(group) == {"app", "app_status", "roles"}
    assert group["app"] == "alpha"
    assert group["app_status"] == "active"
    (r,) = group["roles"]
    assert set(r) == {"assignment_id", "role_id", "role_key", "org_id", "expires_at"}
    assert r["assignment_id"] == str(ra.id)
    assert r["role_id"] == str(role.id)
    assert r["role_key"] == "alpha:viewer"
    assert r["org_id"] is None
    assert datetime.fromisoformat(r["expires_at"]) == exp


async def test_without_include_is_unchanged(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    user = await env.user()
    app = await make_app(db_session, "alpha")
    await assign(db_session, user, await make_role(db_session, app, "alpha:viewer"))
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        resp = await users_client.get(f"{BASE}?email={env.tag}")
    finally:
        event.remove(conn, "before_cursor_execute", _record)
    assert resp.status_code == 200, resp.text
    await db_session.refresh(user)
    expected = Page[UserRead](
        items=[UserRead.model_validate(user)], total=1, limit=20, offset=0
    ).model_dump(mode="json")
    assert resp.json() == expected
    assert list(resp.json()["items"][0]) == list(UserRead.model_fields)
    assert "access" not in resp.text
    assert not any("role_assignments" in s for s in statements)


async def test_get_and_patch_never_carry_access(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    user = await env.user()
    app = await make_app(db_session, "alpha")
    await assign(db_session, user, await make_role(db_session, app, "alpha:viewer"))
    got = await users_client.get(f"{BASE}/{user.id}")
    assert set(got.json()) == USER_READ_KEYS
    patched = await users_client.patch(f"{BASE}/{user.id}", json={"display_name": "Zed"})
    assert patched.status_code == 200, patched.text
    assert set(patched.json()) == USER_READ_KEYS


async def test_user_without_access_gets_empty_list(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    none = await env.user()
    expired = await env.user()
    app = await make_app(db_session, "alpha")
    await assign(
        db_session,
        expired,
        await make_role(db_session, app, "alpha:viewer"),
        expires_at=datetime.now(UTC) - DAY,
    )
    body = await env.list(users_client)
    for user in (none, expired):
        item = env.by_id(body, user)
        assert item["access"] == []
        assert item["access_truncated"] is False


async def test_expired_assignments_excluded(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    user = await env.user()
    app = await make_app(db_session, "alpha")
    now = datetime.now(UTC)
    await assign(
        db_session, user, await make_role(db_session, app, "alpha:a"), expires_at=now - DAY
    )
    await assign(
        db_session, user, await make_role(db_session, app, "alpha:b"), expires_at=now + 30 * DAY
    )
    await assign(db_session, user, await make_role(db_session, app, "alpha:c"))
    item = env.by_id(await env.list(users_client), user)
    assert _flat(item) == [("alpha", "alpha:b", True), ("alpha", "alpha:c", True)]


async def test_app_and_org_scope_rows(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    user = await env.user()
    app = await make_app(db_session, "alpha")
    org = await make_org(db_session)
    viewer = await make_role(db_session, app, "alpha:viewer")
    editor = await make_role(db_session, app, "alpha:editor")
    await assign(db_session, user, viewer, org_id=org.id)
    await assign(db_session, user, viewer)
    await assign(db_session, user, editor, org_id=org.id)
    item = env.by_id(await env.list(users_client), user)
    assert len(item["access"]) == 1
    roles = item["access"][0]["roles"]
    assert [(r["role_key"], r["org_id"]) for r in roles] == [
        ("alpha:editor", str(org.id)),
        ("alpha:viewer", None),
        ("alpha:viewer", str(org.id)),
    ]


async def test_disabled_app_listed_with_status(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    user = await env.user()
    live = await make_app(db_session, "alpha")
    off = await make_app(db_session, "bravo", status="disabled")
    await assign(db_session, user, await make_role(db_session, live, "alpha:viewer"))
    await assign(db_session, user, await make_role(db_session, off, "bravo:viewer"))
    item = env.by_id(await env.list(users_client), user)
    assert [(g["app"], g["app_status"]) for g in item["access"]] == [
        ("alpha", "active"),
        ("bravo", "disabled"),
    ]


async def _many(db: AsyncSession, user: User, app_key: str, count: int, *, offset: int = 0) -> None:
    app = await make_app(db, app_key)
    for i in range(count):
        await assign(db, user, await make_role(db, app, f"{app_key}:r{offset + i:02d}"))


async def test_cap_truncates_at_20_in_order(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    user = await env.user()
    await _many(db_session, user, "alpha", 8)
    await _many(db_session, user, "bravo", 8)
    await _many(db_session, user, "charlie", 6)
    expired = await make_app(db_session, "delta")
    for i in range(3):
        await assign(
            db_session,
            user,
            await make_role(db_session, expired, f"delta:r{i}"),
            expires_at=datetime.now(UTC) - DAY,
        )
    item = env.by_id(await env.list(users_client), user)
    expected = (
        [("alpha", f"alpha:r{i:02d}", True) for i in range(8)]
        + [("bravo", f"bravo:r{i:02d}", True) for i in range(8)]
        + [("charlie", f"charlie:r{i:02d}", True) for i in range(4)]
    )
    assert _flat(item) == expected
    assert len(expected) == 20
    assert item["access_truncated"] is True
    assert [g["app"] for g in item["access"]] == ["alpha", "bravo", "charlie"]


@pytest.mark.parametrize(("count", "truncated"), [(20, False), (21, True)])
async def test_cap_boundary(
    users_client: AsyncClient, env: Env, db_session: AsyncSession, count: int, truncated: bool
) -> None:
    user = await env.user()
    await _many(db_session, user, "alpha", count)
    item = env.by_id(await env.list(users_client), user)
    assert len(_flat(item)) == 20
    assert item["access_truncated"] is truncated


async def test_cap_is_per_user(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    big = await env.user()
    small = await env.user()
    await _many(db_session, big, "alpha", 25)
    await _many(db_session, small, "bravo", 2)
    body = await env.list(users_client)
    assert len(_flat(env.by_id(body, big))) == 20
    assert env.by_id(body, big)["access_truncated"] is True
    assert len(_flat(env.by_id(body, small))) == 2
    assert env.by_id(body, small)["access_truncated"] is False


async def test_access_order(users_client: AsyncClient, env: Env, db_session: AsyncSession) -> None:
    user = await env.user()
    org1, org2 = await make_org(db_session), await make_org(db_session)
    first, second = sorted([org1.id, org2.id])
    apps = {k: await make_app(db_session, k) for k in ("zeta", "alpha", "ab", "a-c")}
    for key in ("zeta", "alpha", "ab", "a-c"):
        await assign(db_session, user, await make_role(db_session, apps[key], f"{key}:b"))
    shared = await make_role(db_session, apps["alpha"], "alpha:a")
    await assign(db_session, user, shared, org_id=second)
    await assign(db_session, user, shared, org_id=first)
    await assign(db_session, user, shared)
    item = env.by_id(await env.list(users_client), user)
    assert [g["app"] for g in item["access"]] == ["a-c", "ab", "alpha", "zeta"]
    alpha = next(g for g in item["access"] if g["app"] == "alpha")
    assert [(r["role_key"], r["org_id"]) for r in alpha["roles"]] == [
        ("alpha:a", None),
        ("alpha:a", str(first)),
        ("alpha:a", str(second)),
        ("alpha:b", None),
    ]


async def _count(
    db: AsyncSession, client: AsyncClient, url: str
) -> tuple[list[str], dict[str, Any]]:
    statements: list[str] = []
    conn = db.bind.sync_connection  # type: ignore[union-attr]

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        resp = await client.get(url)
    finally:
        event.remove(conn, "before_cursor_execute", _record)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return statements, body


async def test_query_count_constant(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "alpha")
    for u in range(5):
        user = await env.user()
        for i in range(3):
            await assign(
                db_session,
                user,
                await make_role(db_session, app, f"alpha:r{u}-{i}"),
            )
    one, body1 = await _count(
        db_session, users_client, f"{BASE}?email={env.tag}&include=access&limit=1"
    )
    many, body50 = await _count(
        db_session, users_client, f"{BASE}?email={env.tag}&include=access&limit=50"
    )
    assert len(body1["items"]) == 1
    assert len(body50["items"]) == 5
    assert len(one) == len(many)
    for statements in (one, many):
        assert sum("FROM role_assignments" in s for s in statements) == 1


async def test_query_count_without_include(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    await env.user()
    statements, _ = await _count(db_session, users_client, f"{BASE}?email={env.tag}")
    assert not any("role_assignments" in s for s in statements)


@pytest.mark.parametrize("value", ["bogus", "", "ACCESS", "access,bogus"])
async def test_include_rejects_bad_values(users_client: AsyncClient, value: str) -> None:
    resp = await users_client.get(f"{BASE}?include={value}")
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert "include" in str(error["details"])


async def test_include_repeated_is_idempotent(users_client: AsyncClient, env: Env) -> None:
    await env.user()
    once = await env.list(users_client, "include=access")
    twice = await env.list(users_client, "include=access&include=access")
    assert once == twice


async def test_include_access_requires_users_read(
    make_client: MakeClient, env: Env, db_session: AsyncSession
) -> None:
    await env.user()
    async with make_client(admin_permissions=["admin:users:read"]) as ok:
        assert "access" in (await env.list(ok))["items"][0]
    async with make_client(admin_permissions=["admin:orgs:read"]) as denied:
        resp = await denied.get(f"{BASE}?include=access")
    assert resp.status_code == 403


async def test_include_access_requires_token(client: AsyncClient) -> None:
    resp = await client.get(f"{BASE}?include=access")
    assert resp.status_code == 401
    assert "error" in resp.json()


async def test_include_access_pagination(
    users_client: AsyncClient, env: Env, db_session: AsyncSession
) -> None:
    for _ in range(5):
        await env.user()
    plain = await env.list(users_client, "limit=2&offset=2")
    withs = await env.list(users_client, "limit=2&offset=2&include=access")
    assert [i["id"] for i in withs["items"]] == [i["id"] for i in plain["items"]]
    assert withs["total"] == plain["total"] == 5
    statements, past = await _count(
        db_session, users_client, f"{BASE}?email={env.tag}&include=access&offset=100"
    )
    assert past["items"] == []
    assert not any("role_assignments" in s for s in statements)
