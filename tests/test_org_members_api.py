import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import RoleAssignment
from app.modules.rbac.repository import RbacRepository
from app.modules.users.models import User, UserStatus
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_app, make_org, make_role, make_user

KEYS = {
    "assignment_id",
    "user_id",
    "email",
    "display_name",
    "user_status",
    "app",
    "role_id",
    "role_key",
    "role_name",
    "granted_by",
    "created_at",
    "expires_at",
    "active",
}


def _url(org_id: object) -> str:
    return f"/api/v1/organizations/{org_id}/members"


@pytest.fixture
async def admin(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
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


async def _set_created(db_session: AsyncSession, ra: RoleAssignment, when: datetime) -> None:
    await db_session.execute(
        update(RoleAssignment)
        .where(RoleAssignment.id == ra.id)
        .values(created_at=when)
        .execution_options(synchronize_session=False)
    )


# --- auth, 404, 422 ---------------------------------------------------------------


async def test_members_401_without_token(client: AsyncClient) -> None:
    assert (await client.get(_url(uuid.uuid4()))).status_code == 401


async def test_members_403_with_organizations_read_only(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org = await make_org(db_session)
    async with make_client(admin_permissions=["admin:organizations:read"]) as c:
        resp = await c.get(_url(org.id))
    assert resp.status_code == 403
    assert resp.json()["error"]["details"]["required"] == "admin:users:read"


async def test_members_admin_star_passes(admin: AsyncClient, db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    assert (await admin.get(_url(org.id))).status_code == 200


async def test_members_users_read_passes(make_client: MakeClient, db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    async with make_client(admin_permissions=["admin:users:read"]) as c:
        assert (await c.get(_url(org.id))).status_code == 200


async def test_members_404_unknown_org(admin: AsyncClient) -> None:
    resp = await admin.get(_url(uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "path_suffix",
    ["", "?limit=101", "?limit=0", "?offset=-1", "?active=maybe", "?app=Bad%20Key"],
)
async def test_members_422_invalid_params(
    admin: AsyncClient, db_session: AsyncSession, path_suffix: str
) -> None:
    org = await make_org(db_session)
    url = _url("not-a-uuid") if path_suffix == "" else _url(org.id) + path_suffix
    assert (await admin.get(url)).status_code == 422


# --- shape ------------------------------------------------------------------------


async def test_members_item_shape(admin: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:r0")
    org = await make_org(db_session)
    user, granter = await make_user(db_session), await make_user(db_session)
    await assign(db_session, user, role, org_id=org.id, granted_by=granter.id)
    resp = await admin.get(_url(org.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    (item,) = body["items"]
    assert set(item) == KEYS
    assert item["app"] == "stoktok"
    assert item["role_key"] == "stoktok:r0"
    assert item["role_name"] == "stoktok:r0"
    assert item["email"] == user.email
    assert item["user_status"] == "active"
    assert item["granted_by"] == str(granter.id)
    assert item["active"] is True
    assert "password_hash" not in resp.text


async def test_members_cache_control_no_store(admin: AsyncClient, db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    assert (await admin.get(_url(org.id))).headers["cache-control"] == "no-store"


async def test_members_two_roles_two_rows(admin: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    r1, r2 = (
        await make_role(db_session, app, "stoktok:a"),
        await make_role(db_session, app, "stoktok:b"),
    )
    org, user = await make_org(db_session), await make_user(db_session)
    await assign(db_session, user, r1, org_id=org.id)
    await assign(db_session, user, r2, org_id=org.id)
    body = (await admin.get(_url(org.id))).json()
    assert body["total"] == 2
    assert {i["role_key"] for i in body["items"]} == {"stoktok:a", "stoktok:b"}
    assert {i["user_id"] for i in body["items"]} == {str(user.id)}


async def test_members_expired_active_false_and_filter(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:r0")
    org = await make_org(db_session)
    now = datetime.now(UTC)
    await assign(db_session, await make_user(db_session), role, org_id=org.id)
    await assign(
        db_session,
        await make_user(db_session),
        role,
        org_id=org.id,
        expires_at=now + timedelta(days=1),
    )
    await assign(
        db_session,
        await make_user(db_session),
        role,
        org_id=org.id,
        expires_at=now - timedelta(days=1),
    )
    everything = (await admin.get(_url(org.id))).json()
    assert everything["total"] == 3
    assert sorted(i["active"] for i in everything["items"]) == [False, True, True]
    live = (await admin.get(_url(org.id), params={"active": "true"})).json()
    expired = (await admin.get(_url(org.id), params={"active": "false"})).json()
    assert (live["total"], len(live["items"])) == (2, 2)
    assert (expired["total"], len(expired["items"])) == (1, 1)
    assert expired["items"][0]["active"] is False


async def test_members_app_filter_and_total(admin: AsyncClient, db_session: AsyncSession) -> None:
    a1, a2 = await make_app(db_session, "stoktok"), await make_app(db_session, "calechip")
    r1, r2 = (
        await make_role(db_session, a1, "stoktok:r0"),
        await make_role(db_session, a2, "calechip:r0"),
    )
    org = await make_org(db_session)
    await assign(db_session, await make_user(db_session), r1, org_id=org.id)
    await assign(db_session, await make_user(db_session), r2, org_id=org.id)
    await assign(db_session, await make_user(db_session), r2, org_id=org.id)
    body = (await admin.get(_url(org.id), params={"app": "calechip"})).json()
    assert body["total"] == 2
    assert {i["app"] for i in body["items"]} == {"calechip"}
    unknown = await admin.get(_url(org.id), params={"app": "nope"})
    assert unknown.status_code == 200
    assert unknown.json()["items"] == [] and unknown.json()["total"] == 0


async def test_members_only_this_org_scoped_rows(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:r0")
    org, other = await make_org(db_session), await make_org(db_session)
    mine = await make_user(db_session)
    app_scoped = await make_user(db_session)
    elsewhere = await make_user(db_session)
    await assign(db_session, mine, role, org_id=org.id)
    await assign(db_session, app_scoped, role)
    await assign(db_session, elsewhere, role, org_id=other.id)
    body = (await admin.get(_url(org.id))).json()
    assert body["total"] == 1
    assert [i["user_id"] for i in body["items"]] == [str(mine.id)]


async def test_members_disabled_user_listed(admin: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:r0")
    org, user = await make_org(db_session), await make_user(db_session)
    user.status = UserStatus.DISABLED.value
    await db_session.flush()
    await assign(db_session, user, role, org_id=org.id)
    (item,) = (await admin.get(_url(org.id))).json()["items"]
    assert item["user_status"] == "disabled"


async def test_members_paging_and_order(admin: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:r0")
    org = await make_org(db_session)
    base = datetime.now(UTC) - timedelta(days=10)
    users: list[User] = []
    for i in range(3):
        user = await make_user(db_session)
        users.append(user)
        await _set_created(
            db_session,
            await assign(db_session, user, role, org_id=org.id),
            base + timedelta(days=i),
        )
    expected = [str(u.id) for u in reversed(users)]  # newest first
    full = (await admin.get(_url(org.id))).json()
    assert [i["user_id"] for i in full["items"]] == expected
    page = (await admin.get(_url(org.id), params={"limit": 1, "offset": 1})).json()
    assert [i["user_id"] for i in page["items"]] == expected[1:2]
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    beyond = (await admin.get(_url(org.id), params={"offset": 10})).json()
    assert beyond["items"] == [] and beyond["total"] == 3


async def test_members_empty_org(admin: AsyncClient, db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    body = (await admin.get(_url(org.id))).json()
    assert body["items"] == [] and body["total"] == 0


# --- no N+1 -----------------------------------------------------------------------


async def test_members_bounded_queries(admin: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:r0")
    org = await make_org(db_session)
    for _ in range(6):
        await assign(db_session, await make_user(db_session), role, org_id=org.id)
    counts = []
    for limit in (1, 6):
        with _capture_sql(db_session) as statements:
            resp = await admin.get(_url(org.id), params={"limit": limit})
        assert len(resp.json()["items"]) == limit
        counts.append(len(statements))
    assert counts[0] == counts[1]


async def test_page_org_members_two_statements(db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:r0")
    org = await make_org(db_session)
    for _ in range(3):
        await assign(db_session, await make_user(db_session), role, org_id=org.id)
    with _capture_sql(db_session) as statements:
        rows, total = await RbacRepository(db_session).page_org_members(
            org.id, app_key=None, active=None, limit=10, offset=0
        )
    assert (len(rows), total) == (3, 3)
    assert len(statements) == 2
