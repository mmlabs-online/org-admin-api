"""USR-35: users list filters `app` and `role_id` (spec §5.15 Part 2)."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_app, make_org, make_role

BASE = "/api/v1/users"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def users_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"]) as c:
        yield c


async def _user(
    db: AsyncSession,
    email: str | None = None,
    *,
    status: str = "active",
    created_at: datetime | None = None,
) -> User:
    user = User(
        email=email or f"{uuid.uuid4().hex[:12]}@example.test", password_hash="h", status=status
    )
    if created_at is not None:
        user.created_at = created_at
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


def _ids(body: dict[str, Any]) -> set[str]:
    return {item["id"] for item in body["items"]}


def _sid(*users: User) -> set[str]:
    return {str(u.id) for u in users}


async def _fixture(db: AsyncSession) -> dict[str, Any]:
    """App A (`a:viewer`, `a:editor`), app B (`b:viewer`), org O; U1 app scope, U2 org scope."""
    app_a = await make_app(db, "flt-a")
    app_b = await make_app(db, "flt-b")
    a_viewer = await make_role(db, app_a, "flt-a:viewer")
    a_editor = await make_role(db, app_a, "flt-a:editor")
    b_viewer = await make_role(db, app_b, "flt-b:viewer")
    org = await make_org(db)
    u1, u2, u3, u4 = [await _user(db) for _ in range(4)]
    await assign(db, u1, a_viewer)
    await assign(db, u2, a_editor, org_id=org.id)
    await assign(db, u3, b_viewer)
    return {
        "app_a": app_a, "a_viewer": a_viewer, "a_editor": a_editor, "b_viewer": b_viewer,
        "org": org, "u1": u1, "u2": u2, "u3": u3, "u4": u4,
    }  # fmt: skip


async def test_app_filter_matches_app_and_org_scope(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    f = await _fixture(db_session)
    resp = await users_client.get(BASE, params={"app": "flt-a"})
    assert resp.status_code == 200
    body = resp.json()
    assert _ids(body) == _sid(f["u1"], f["u2"])
    assert body["total"] == 2


async def test_role_filter_matches_app_and_org_scope(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    f = await _fixture(db_session)
    u5 = await _user(db_session)
    await assign(db_session, u5, f["a_viewer"], org_id=f["org"].id)
    resp = await users_client.get(BASE, params={"role_id": str(f["a_viewer"].id)})
    assert _ids(resp.json()) == _sid(f["u1"], u5)
    resp = await users_client.get(BASE, params={"role_id": str(f["a_editor"].id)})
    assert _ids(resp.json()) == _sid(f["u2"])


async def test_app_and_role_filter_together(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    f = await _fixture(db_session)
    u5 = await _user(db_session)
    await assign(db_session, u5, f["a_viewer"], org_id=f["org"].id)
    role_only = await users_client.get(BASE, params={"role_id": str(f["a_viewer"].id)})
    both = await users_client.get(BASE, params={"app": "flt-a", "role_id": str(f["a_viewer"].id)})
    assert both.status_code == 200
    assert _ids(both.json()) == _ids(role_only.json()) == _sid(f["u1"], u5)
    assert both.json()["total"] == role_only.json()["total"] == 2


@pytest.mark.parametrize("by", ["app", "role_id"])
async def test_expired_only_excluded(
    users_client: AsyncClient, db_session: AsyncSession, by: str
) -> None:
    f = await _fixture(db_session)
    now = datetime.now(UTC)
    u6, u7, u8 = [await _user(db_session) for _ in range(3)]
    role = f["a_viewer"]
    await assign(db_session, u6, role, expires_at=now - timedelta(days=1))
    await assign(db_session, u7, role, org_id=f["org"].id, expires_at=now - timedelta(days=1))
    await assign(db_session, u7, role)
    await assign(db_session, u8, role, expires_at=now + timedelta(days=30))
    value = "flt-a" if by == "app" else str(role.id)
    resp = await users_client.get(BASE, params={by: value})
    ids = _ids(resp.json())
    assert str(u6.id) not in ids
    assert {str(u7.id), str(u8.id), str(f["u1"].id)} <= ids


async def test_disabled_app_accepted(users_client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "flt-off", status="disabled")
    role = await make_role(db_session, app, "flt-off:viewer")
    u = await _user(db_session)
    await assign(db_session, u, role)
    for params in (
        {"app": "flt-off"},
        {"role_id": str(role.id)},
        {"app": "flt-off", "role_id": str(role.id)},
    ):
        resp = await users_client.get(BASE, params=params)
        assert resp.status_code == 200
        assert _ids(resp.json()) == _sid(u)


async def test_total_is_distinct(users_client: AsyncClient, db_session: AsyncSession) -> None:
    f = await _fixture(db_session)
    o2 = await make_org(db_session)
    u = await _user(db_session)
    await assign(db_session, u, f["a_viewer"])
    await assign(db_session, u, f["a_viewer"], org_id=f["org"].id)
    await assign(db_session, u, f["a_viewer"], org_id=o2.id)
    await assign(db_session, u, f["a_editor"])
    # only this user holds a:editor at app scope plus a:viewer in two orgs; U1/U2 also match
    resp = await users_client.get(BASE, params={"app": "flt-a"})
    body = resp.json()
    assert body["total"] == 3
    assert [i["id"] for i in body["items"]].count(str(u.id)) == 1
    resp = await users_client.get(BASE, params={"role_id": str(f["a_viewer"].id)})
    body = resp.json()
    assert body["total"] == 2
    assert [i["id"] for i in body["items"]].count(str(u.id)) == 1


async def test_filters_combine_with_email(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    f = await _fixture(db_session)
    tag = uuid.uuid4().hex[:8]
    alpha = await _user(db_session, f"{tag}-alpha@example.test")
    beta = await _user(db_session, f"{tag}-beta@example.test")
    await _user(db_session, f"{tag}-alpha2@example.test")
    await assign(db_session, alpha, f["a_viewer"])
    await assign(db_session, beta, f["a_viewer"])
    resp = await users_client.get(BASE, params={"app": "flt-a", "email": f"{tag}-alpha@"})
    body = resp.json()
    assert _ids(body) == _sid(alpha)
    assert body["total"] == 1


async def test_filters_combine_with_status(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    f = await _fixture(db_session)
    off = await _user(db_session, status="disabled")
    await assign(db_session, off, f["a_viewer"])
    resp = await users_client.get(BASE, params={"status": "disabled", "app": "flt-a"})
    assert _ids(resp.json()) == _sid(off)
    resp = await users_client.get(
        BASE, params={"status": "active", "role_id": str(f["a_viewer"].id)}
    )
    assert _ids(resp.json()) == _sid(f["u1"])


async def test_filters_paginate(users_client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "flt-pg")
    role = await make_role(db_session, app, "flt-pg:viewer")
    holders = []
    for i in range(3):
        u = await _user(db_session, created_at=T0 + timedelta(days=i))
        await assign(db_session, u, role)
        holders.append(u)
    await _user(db_session, created_at=T0 + timedelta(days=10))
    newest_first = [str(u.id) for u in reversed(holders)]
    resp = await users_client.get(BASE, params={"app": "flt-pg", "limit": 2, "offset": 0})
    body = resp.json()
    assert [i["id"] for i in body["items"]] == newest_first[:2]
    assert body["total"] == 3
    resp = await users_client.get(BASE, params={"app": "flt-pg", "limit": 2, "offset": 2})
    body = resp.json()
    assert [i["id"] for i in body["items"]] == newest_first[2:]
    assert body["total"] == 3
    resp = await users_client.get(BASE, params={"app": "flt-pg", "limit": 2, "offset": 10})
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 3


async def test_filters_with_include_access_not_narrowed(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    f = await _fixture(db_session)
    u = await _user(db_session)
    await assign(db_session, u, f["a_viewer"])
    await assign(db_session, u, f["b_viewer"])
    for params in (
        {"app": "flt-a", "include": "access"},
        {"role_id": str(f["a_viewer"].id), "include": "access"},
    ):
        resp = await users_client.get(BASE, params=params)
        assert resp.status_code == 200
        body = resp.json()
        assert str(f["u3"].id) not in _ids(body)
        item = next(i for i in body["items"] if i["id"] == str(u.id))
        assert {g["app"] for g in item["access"]} == {"flt-a", "flt-b"}


def _unprocessable(resp: Any, field: str, reason: str) -> None:
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "unprocessable"
    assert error["details"] == [{"field": field, "reason": reason}]


async def test_unknown_app_422(users_client: AsyncClient) -> None:
    resp = await users_client.get(BASE, params={"app": "no-such-app"})
    _unprocessable(resp, "app", "unknown_app")
    assert "no-such-app" not in resp.text


async def test_unknown_role_422(users_client: AsyncClient, db_session: AsyncSession) -> None:
    await _fixture(db_session)
    resp = await users_client.get(BASE, params={"app": "flt-a", "role_id": str(uuid.uuid4())})
    _unprocessable(resp, "role_id", "unknown_role")
    resp = await users_client.get(BASE, params={"role_id": str(uuid.uuid4())})
    _unprocessable(resp, "role_id", "unknown_role")


async def test_role_not_in_app_422(users_client: AsyncClient, db_session: AsyncSession) -> None:
    f = await _fixture(db_session)
    resp = await users_client.get(BASE, params={"app": "flt-a", "role_id": str(f["b_viewer"].id)})
    _unprocessable(resp, "role_id", "role_not_in_app")


async def test_unknown_app_wins_over_unknown_role(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    f = await _fixture(db_session)
    resp = await users_client.get(BASE, params={"app": "no-such-app", "role_id": str(uuid.uuid4())})
    _unprocessable(resp, "app", "unknown_app")
    resp = await users_client.get(
        BASE, params={"app": "no-such-app", "role_id": str(f["a_viewer"].id)}
    )
    _unprocessable(resp, "app", "unknown_app")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("app", ""),
        ("app", "Bad!"),
        ("app", "a"),
        ("app", "a" * 33),
        ("app", "9app"),
        ("role_id", ""),
        ("role_id", "not-a-uuid"),
        ("role_id", "123"),
    ],
)
async def test_malformed_filters_422(users_client: AsyncClient, name: str, value: str) -> None:
    resp = await users_client.get(BASE, params={name: value})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["query", name]


async def test_filters_require_token(client: AsyncClient, db_session: AsyncSession) -> None:
    await make_app(db_session, "flt-a")
    resp = await client.get(BASE, params={"app": "flt-a"})
    assert resp.status_code == 401


async def test_filters_require_users_read(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "flt-a")
    async with make_client(admin_permissions=["admin:orgs:read"]) as c:
        resp = await c.get(BASE, params={"app": "flt-a"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"
    async with make_client(admin_permissions=["admin:users:read"]) as c:
        resp = await c.get(BASE, params={"app": "flt-a"})
    assert resp.status_code == 200
