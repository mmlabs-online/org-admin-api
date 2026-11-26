import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, Role
from tests.conftest import MakeClient
from tests.rbac_factories import (
    grant,
    make_app,
    make_permission,
    make_role,
    make_user,
)

BASE = "/api/v1/apps"
KEYS = {
    "id",
    "app",
    "key",
    "name",
    "is_system",
    "is_default",
    "is_super_admin",
    "privileged",
    "permissions",
    "created_at",
    "updated_at",
}


def _url(app: str = "stoktok", role_id: object | None = None) -> str:
    base = f"{BASE}/{app}/role-definitions"
    return base if role_id is None else f"{base}/{role_id}"


@pytest.fixture
async def rbac_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"]) as c:
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


async def _roles(db_session: AsyncSession, n: int = 1) -> tuple[App, list[Role]]:
    app = await make_app(db_session, "stoktok")
    roles = [await make_role(db_session, app, f"stoktok:r{i}") for i in range(n)]
    return app, roles


async def test_role_definitions_item_shape(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, (role,) = await _roles(db_session)
    c = await make_permission(db_session, app, "stoktok:c:read")
    a = await make_permission(db_session, app, "stoktok:a:read")
    await grant(db_session, role, c, a)
    resp = await rbac_client.get(_url())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert set(item) == KEYS
    assert item["app"] == "stoktok"
    assert item["id"] == str(role.id)
    assert item["permissions"] == ["stoktok:a:read", "stoktok:c:read"]
    assert item["created_at"] and item["updated_at"]


async def test_role_definitions_paging_and_order(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, roles = await _roles(db_session, 5)
    seen: list[str] = []
    for offset in (0, 2, 4):
        resp = await rbac_client.get(_url(), params={"limit": 2, "offset": offset})
        body = resp.json()
        assert body["total"] == 5
        assert (body["limit"], body["offset"]) == (2, offset)
        seen += [i["key"] for i in body["items"]]
    assert seen == sorted(r.key for r in roles)
    assert (await rbac_client.get(_url())).json()["limit"] == 20


async def test_role_definitions_limit_over_100_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _roles(db_session)
    for params in ({"limit": 101}, {"offset": -1}):
        resp = await rbac_client.get(_url(), params=params)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


async def test_role_definitions_is_default(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, (a, b) = await _roles(db_session, 2)
    flags = {i["id"]: i["is_default"] for i in (await rbac_client.get(_url())).json()["items"]}
    assert flags == {str(a.id): False, str(b.id): False}
    app.default_role_id = a.id
    await db_session.flush()
    flags = {i["id"]: i["is_default"] for i in (await rbac_client.get(_url())).json()["items"]}
    assert flags == {str(a.id): True, str(b.id): False}


async def test_role_definitions_privileged_flags(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, (priv, plain, empty) = await _roles(db_session, 3)
    await grant(
        db_session,
        priv,
        await make_permission(db_session, app, "stoktok:x:manage", is_admin=True),
    )
    await grant(db_session, plain, await make_permission(db_session, app, "stoktok:x:read"))
    items = {i["id"]: i for i in (await rbac_client.get(_url())).json()["items"]}
    assert items[str(priv.id)]["privileged"] is True
    assert items[str(plain.id)]["privileged"] is False
    assert items[str(empty.id)]["privileged"] is False

    admin_app = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin_app is not None
    await make_role(db_session, admin_app, "admin:empty")
    admin_items = (await rbac_client.get(_url("admin"), params={"limit": 100})).json()["items"]
    assert {"admin:empty", "admin:super-admin"} <= {i["key"] for i in admin_items}
    assert all(i["privileged"] for i in admin_items)


async def test_privileged_flag_matches_assignment_guard(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, (priv, plain) = await _roles(db_session, 2)
    await grant(
        db_session, priv, await make_permission(db_session, app, "stoktok:x:manage", is_admin=True)
    )
    actor = await make_user(db_session)
    target = await make_user(db_session)
    async with make_client(
        admin_permissions=["admin:roles:assign", "admin:apps:read"], admin_user=actor
    ) as c:
        items = {i["id"]: i for i in (await c.get(_url())).json()["items"]}
        post = f"/api/v1/users/{target.id}/role-assignments"
        assert items[str(plain.id)]["privileged"] is False
        ok = await c.post(post, json={"role_id": str(plain.id)})
        assert ok.status_code == 201, ok.text
        # last: a denied request rolls the shared test transaction back
        assert items[str(priv.id)]["privileged"] is True
        resp = await c.post(post, json={"role_id": str(priv.id)})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "admin_wildcard_required"


async def test_role_definitions_admin_app_listed_with_leaf_permission(
    make_client: MakeClient,
) -> None:
    async with make_client(admin_permissions=["admin:apps:read"]) as c:
        resp = await c.get(_url("admin"), params={"limit": 100})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert "admin:super-admin" in {i["key"] for i in items}
    assert all(i["privileged"] for i in items)


async def test_role_definitions_disabled_app_listed(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "oldapp", status="disabled")
    role = await make_role(db_session, app, "oldapp:viewer")
    resp = await rbac_client.get(_url("oldapp"))
    assert resp.status_code == 200
    assert [i["id"] for i in resp.json()["items"]] == [str(role.id)]


async def test_role_definitions_unknown_app_404(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.get(_url("nope"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_role_definitions_malformed_key_404(rbac_client: AsyncClient) -> None:
    for key in ("BAD_KEY!", "a"):
        assert (await rbac_client.get(_url(key))).status_code == 404


async def test_role_definitions_403_without_permission(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await _roles(db_session)
    async with make_client(admin_permissions=["admin:roles:assign"]) as c:
        for app in ("stoktok", "nope"):
            resp = await c.get(_url(app))
            assert resp.status_code == 403
            assert resp.json()["error"]["details"]["required"] == "admin:apps:read"
    for perms in (["admin:roles:assign", "admin:apps:read"], ["admin:*"]):
        async with make_client(admin_permissions=perms) as c:
            assert (await c.get(_url())).status_code == 200


async def test_role_definitions_401_before_404(client: AsyncClient) -> None:
    assert (await client.get(_url("nope"))).status_code == 401


async def test_role_definition_detail(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    app, (role, _) = await _roles(db_session, 2)
    await grant(db_session, role, await make_permission(db_session, app, "stoktok:a:read"))
    listed = {i["id"]: i for i in (await rbac_client.get(_url())).json()["items"]}
    resp = await rbac_client.get(_url(role_id=role.id))
    assert resp.status_code == 200
    assert resp.json() == listed[str(role.id)]


async def test_role_definition_detail_404_unknown_role_and_other_app(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, (role,) = await _roles(db_session)
    other = await make_app(db_session, "other")
    other_role = await make_role(db_session, other, "other:viewer")
    assert (await rbac_client.get(_url(role_id=uuid.uuid4()))).status_code == 404
    assert (await rbac_client.get(_url(role_id=other_role.id))).status_code == 404
    assert (await rbac_client.get(_url("nope", role.id))).status_code == 404


async def test_role_definition_detail_bad_uuid_422(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.get(_url(role_id="not-a-uuid"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_role_definitions_bounded_queries(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, roles = await _roles(db_session, 6)
    perm = await make_permission(db_session, app, "stoktok:a:read")
    for r in roles:
        await grant(db_session, r, perm)
    counts = []
    for limit in (1, 6):
        with _capture_sql(db_session) as statements:
            resp = await rbac_client.get(_url(), params={"limit": limit})
        assert len(resp.json()["items"]) == limit
        counts.append(len(statements))
    assert counts[0] == counts[1]


async def test_catalog_unchanged_and_consistent(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, (role,) = await _roles(db_session)
    await grant(db_session, role, await make_permission(db_session, app, "stoktok:a:read"))
    await make_app(db_session, "oldapp", status="disabled")
    assert (await rbac_client.get(f"{BASE}/admin/roles")).status_code == 404
    assert (await rbac_client.get(f"{BASE}/oldapp/roles")).status_code == 404
    catalog = (await rbac_client.get(f"{BASE}/stoktok/roles")).text
    assert str(role.id) not in catalog
    cat = (await rbac_client.get(f"{BASE}/stoktok/roles")).json()["roles"]
    defs = (await rbac_client.get(_url())).json()["items"]
    assert {r["key"]: r["permissions"] for r in cat} == {d["key"]: d["permissions"] for d in defs}
