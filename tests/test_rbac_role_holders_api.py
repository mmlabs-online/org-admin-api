import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.rbac.repository import RbacRepository
from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.rbac_factories import (
    assign,
    make_app,
    make_org,
    make_role,
    make_user,
    super_admin_role,
)

BASE = "/api/v1/apps"
KEYS = {
    "id",
    "app",
    "role_id",
    "role_key",
    "role_name",
    "org_id",
    "org_name",
    "granted_by",
    "expires_at",
    "active",
    "created_at",
    "user_id",
    "email",
    "display_name",
    "user_status",
}


def _url(app: str, role_id: object) -> str:
    return f"{BASE}/{app}/role-definitions/{role_id}/assignments"


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


async def _setup(db_session: AsyncSession, **app_kwargs: object) -> tuple[App, Role]:
    app = await make_app(db_session, "stoktok", **app_kwargs)
    return app, await make_role(db_session, app, "stoktok:r0")


def _now() -> datetime:
    return datetime.now(UTC)


async def _set_created(db_session: AsyncSession, ra: RoleAssignment, when: datetime) -> None:
    await db_session.execute(
        update(RoleAssignment)
        .where(RoleAssignment.id == ra.id)
        .values(created_at=when)
        .execution_options(synchronize_session=False)
    )


# --- auth ---------------------------------------------------------------------


async def test_holders_401_without_token(client: AsyncClient) -> None:
    resp = await client.get(_url("nope", uuid.uuid4()))
    assert resp.status_code == 401


async def test_holders_403_without_users_read(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=["admin:orgs:read"]) as c:
        resp = await c.get(_url("nope", uuid.uuid4()))
    assert resp.status_code == 403
    assert resp.json()["error"]["details"]["required"] == "admin:users:read"


@pytest.mark.parametrize("permission", ["admin:apps:read", "admin:roles:assign"])
async def test_holders_403_with_other_permission_only(
    make_client: MakeClient, db_session: AsyncSession, permission: str
) -> None:
    _, role = await _setup(db_session)
    async with make_client(admin_permissions=[permission]) as c:
        resp = await c.get(_url("stoktok", role.id))
    assert resp.status_code == 403
    assert resp.json()["error"]["details"]["required"] == "admin:users:read"


async def test_holders_admin_star_passes(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    assert (await rbac_client.get(_url("stoktok", role.id))).status_code == 200


# --- 404 / 422 ----------------------------------------------------------------


async def test_holders_404_unknown_app(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.get(_url("nope", uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "App not found"


async def test_holders_404_unknown_role(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    await _setup(db_session)
    resp = await rbac_client.get(_url("stoktok", uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "Role not found"


async def test_holders_404_other_app_role_same_body(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _setup(db_session)
    other = await make_app(db_session, "other")
    other_role = await make_role(db_session, other, "other:viewer")
    unknown = await rbac_client.get(_url("stoktok", uuid.uuid4()))
    foreign = await rbac_client.get(_url("stoktok", other_role.id))
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()


async def test_holders_invalid_params_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    assert (await rbac_client.get(_url("stoktok", "not-a-uuid"))).status_code == 422
    for params in ({"org_id": "not-a-uuid"}, {"active": "maybe"}):
        resp = await rbac_client.get(_url("stoktok", role.id), params=params)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


async def test_holders_limit_over_100_is_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    resp = await rbac_client.get(_url("stoktok", role.id), params={"limit": 101})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- shape --------------------------------------------------------------------


async def test_holders_org_scope_names(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    role.name = "Viewer"
    org = await make_org(db_session, name="Acme")
    await assign(db_session, await make_user(db_session), role, org_id=org.id)
    await assign(db_session, await make_user(db_session), role)
    await db_session.flush()
    items = (await rbac_client.get(_url("stoktok", role.id))).json()["items"]
    assert {i["org_name"] for i in items} == {"Acme", None}
    assert {i["role_name"] for i in items} == {"Viewer"}
    for i in items:
        assert (i["org_name"] is None) == (i["org_id"] is None)
    filtered = await rbac_client.get(_url("stoktok", role.id), params={"org_id": str(org.id)})
    assert [i["org_name"] for i in filtered.json()["items"]] == ["Acme"]


async def test_holders_item_shape(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    user = await make_user(db_session)
    user.display_name = "Holder One"
    granter = await make_user(db_session)
    ra = await assign(db_session, user, role, granted_by=granter.id)
    resp = await rbac_client.get(_url("stoktok", role.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert set(item) == KEYS
    assert item["id"] == str(ra.id)
    assert item["app"] == "stoktok"
    assert item["role_id"] == str(role.id)
    assert item["role_key"] == "stoktok:r0"
    assert item["role_name"] == "stoktok:r0"
    assert item["org_id"] is None
    assert item["org_name"] is None
    assert item["granted_by"] == str(granter.id)
    assert item["expires_at"] is None
    assert item["active"] is True
    assert item["user_id"] == str(user.id)
    assert item["email"] == user.email
    assert item["display_name"] == "Holder One"
    assert item["user_status"] == "active"
    assert "password_hash" not in resp.text


async def test_holders_cache_control_no_store(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    resp = await rbac_client.get(_url("stoktok", role.id))
    assert resp.headers["cache-control"] == "no-store"


# --- filters ------------------------------------------------------------------


async def test_holders_expired_default_and_active_filters(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    live = await assign(db_session, await make_user(db_session), role)
    dead = await assign(
        db_session, await make_user(db_session), role, expires_at=_now() - timedelta(days=1)
    )
    url = _url("stoktok", role.id)
    body = (await rbac_client.get(url)).json()
    assert body["total"] == 2
    flags = {i["id"]: i["active"] for i in body["items"]}
    assert flags == {str(live.id): True, str(dead.id): False}
    body = (await rbac_client.get(url, params={"active": "true"})).json()
    assert [i["id"] for i in body["items"]] == [str(live.id)]
    assert body["total"] == 1
    body = (await rbac_client.get(url, params={"active": "false"})).json()
    assert [i["id"] for i in body["items"]] == [str(dead.id)]
    assert body["total"] == 1


async def test_holders_active_boundary(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    never = await assign(db_session, await make_user(db_session), role)
    future = await assign(
        db_session, await make_user(db_session), role, expires_at=_now() + timedelta(days=1)
    )
    past = await assign(
        db_session, await make_user(db_session), role, expires_at=_now() - timedelta(seconds=1)
    )
    items = (await rbac_client.get(_url("stoktok", role.id))).json()["items"]
    assert {i["id"]: i["active"] for i in items} == {
        str(never.id): True,
        str(future.id): True,
        str(past.id): False,
    }


async def test_holders_org_filter(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session, org_mode="required")
    org_a, org_b = await make_org(db_session), await make_org(db_session)
    app_row = await assign(db_session, await make_user(db_session), role)
    a_row = await assign(db_session, await make_user(db_session), role, org_id=org_a.id)
    await assign(db_session, await make_user(db_session), role, org_id=org_b.id)
    url = _url("stoktok", role.id)
    body = (await rbac_client.get(url)).json()
    assert body["total"] == 3
    assert {i["id"] for i in body["items"]} >= {str(app_row.id), str(a_row.id)}
    body = (await rbac_client.get(url, params={"org_id": str(org_a.id)})).json()
    assert [i["id"] for i in body["items"]] == [str(a_row.id)]
    assert body["items"][0]["org_id"] == str(org_a.id)


async def test_holders_unknown_org_is_empty_page(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    await assign(db_session, await make_user(db_session), role)
    resp = await rbac_client.get(_url("stoktok", role.id), params={"org_id": str(uuid.uuid4())})
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["total"] == 0


async def test_holders_filters_combine_and_total(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session, org_mode="required")
    org = await make_org(db_session)
    live = await assign(db_session, await make_user(db_session), role, org_id=org.id)
    await assign(db_session, await make_user(db_session), role, org_id=org.id)
    dead = await assign(
        db_session,
        await make_user(db_session),
        role,
        org_id=org.id,
        expires_at=_now() - timedelta(days=1),
    )
    await assign(db_session, await make_user(db_session), role)  # app scope
    url = _url("stoktok", role.id)
    body = (
        await rbac_client.get(url, params={"org_id": str(org.id), "active": "false", "limit": 1})
    ).json()
    assert [i["id"] for i in body["items"]] == [str(dead.id)]
    assert body["total"] == 1
    body = (
        await rbac_client.get(url, params={"org_id": str(org.id), "active": "true", "limit": 1})
    ).json()
    assert len(body["items"]) == 1
    assert body["total"] == 2
    assert live.id  # one of the two live org rows


async def test_holders_same_user_multiple_rows(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session, org_mode="required")
    org = await make_org(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, role)
    await assign(db_session, user, role, org_id=org.id)
    body = (await rbac_client.get(_url("stoktok", role.id))).json()
    assert body["total"] == 2
    assert [i["user_id"] for i in body["items"]] == [str(user.id)] * 2


async def test_holders_only_this_role(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    app, role = await _setup(db_session)
    other_role = await make_role(db_session, app, "stoktok:r1")
    mine = await assign(db_session, await make_user(db_session), role)
    await assign(db_session, await make_user(db_session), other_role)
    body = (await rbac_client.get(_url("stoktok", role.id))).json()
    assert [i["id"] for i in body["items"]] == [str(mine.id)]


# --- order and paging ---------------------------------------------------------


async def test_holders_order_and_id_tiebreak(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    base = _now() - timedelta(days=10)
    old = await assign(db_session, await make_user(db_session), role)
    mid_a = await assign(db_session, await make_user(db_session), role)
    mid_b = await assign(db_session, await make_user(db_session), role)
    new = await assign(db_session, await make_user(db_session), role)
    await _set_created(db_session, old, base)
    await _set_created(db_session, mid_a, base + timedelta(days=1))
    await _set_created(db_session, mid_b, base + timedelta(days=1))
    await _set_created(db_session, new, base + timedelta(days=2))
    ids = [i["id"] for i in (await rbac_client.get(_url("stoktok", role.id))).json()["items"]]
    tied = sorted([str(mid_a.id), str(mid_b.id)], key=uuid.UUID)
    assert ids == [str(new.id), *tied, str(old.id)]


async def test_holders_paging(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    rows = [await assign(db_session, await make_user(db_session), role) for _ in range(3)]
    url = _url("stoktok", role.id)
    first = (await rbac_client.get(url, params={"limit": 2, "offset": 0})).json()
    second = (await rbac_client.get(url, params={"limit": 2, "offset": 2})).json()
    assert (first["total"], first["limit"], first["offset"]) == (3, 2, 0)
    assert (second["total"], second["limit"], second["offset"]) == (3, 2, 2)
    a = [i["id"] for i in first["items"]]
    b = [i["id"] for i in second["items"]]
    assert len(a) == 2 and len(b) == 1
    assert not set(a) & set(b)
    assert set(a) | set(b) == {str(r.id) for r in rows}


# --- who is listed ------------------------------------------------------------


async def test_holders_disabled_user_listed(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, role)
    await db_session.execute(update(User).where(User.id == user.id).values(status="disabled"))
    item = (await rbac_client.get(_url("stoktok", role.id))).json()["items"][0]
    assert item["user_status"] == "disabled"
    assert item["active"] is True


async def test_holders_admin_app_super_admin_listed(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    role = await super_admin_role(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, role)
    async with make_client(admin_permissions=["admin:users:read"]) as c:
        resp = await c.get(_url("admin", role.id))
    assert resp.status_code == 200
    assert str(user.id) in [i["user_id"] for i in resp.json()["items"]]


async def test_holders_disabled_app_listed(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "oldapp", status="disabled")
    role = await make_role(db_session, app, "oldapp:viewer")
    await assign(db_session, await make_user(db_session), role)
    resp = await rbac_client.get(_url("oldapp", role.id))
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# --- queries ------------------------------------------------------------------


async def test_holders_bounded_queries(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    for _ in range(6):
        await assign(db_session, await make_user(db_session), role)
    counts = []
    for limit in (1, 6):
        with _capture_sql(db_session) as statements:
            resp = await rbac_client.get(_url("stoktok", role.id), params={"limit": limit})
        assert len(resp.json()["items"]) == limit
        counts.append(len(statements))
    assert counts[0] == counts[1]


async def test_page_role_holders_two_statements(db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    await assign(db_session, await make_user(db_session), role)
    with _capture_sql(db_session) as statements:
        rows, total = await RbacRepository(db_session).page_role_holders(
            role.id, active=None, org_id=None, limit=10, offset=0
        )
    assert len(statements) == 2
    assert (len(rows), total) == (1, 1)
