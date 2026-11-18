import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.users.models import UserStatus
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_app, make_org, make_role, make_user, super_admin_role

BASE = "/api/v1/apps"
KEYS = {
    "id", "app", "role_id", "role_key", "org_id", "granted_by", "expires_at", "active",
    "created_at", "user_id", "email", "display_name", "user_status", "email_verified",
}  # fmt: skip
PAST = datetime(2020, 1, 1, tzinfo=UTC)


def _url(role_id: object, app: str = "stoktok") -> str:
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


async def _role(db_session: AsyncSession, key: str = "stoktok") -> tuple[App, Role]:
    app = await make_app(db_session, key)
    return app, await make_role(db_session, app, f"{key}:viewer")


async def _ids(client: AsyncClient, role: Role, **params: Any) -> tuple[list[str], int]:
    resp = await client.get(_url(role.id), params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return [i["id"] for i in body["items"]], body["total"]


async def test_role_holders_item_shape(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _role(db_session)
    user = await make_user(db_session)
    user.display_name = "Some One"
    user.email_verified_at = datetime.now(UTC)
    granter = await make_user(db_session)
    ra = await assign(db_session, user, role, granted_by=granter.id)
    resp = await rbac_client.get(_url(role.id))
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert (body["total"], body["limit"], body["offset"]) == (1, 20, 0)
    item = body["items"][0]
    assert set(item) == KEYS
    assert item["id"] == str(ra.id)
    assert item["app"] == "stoktok"
    assert item["role_id"] == str(role.id)
    assert item["role_key"] == role.key
    assert item["org_id"] is None
    assert item["granted_by"] == str(granter.id)
    assert item["expires_at"] is None
    assert item["active"] is True
    assert item["user_id"] == str(user.id)
    assert item["email"] == user.email
    assert item["display_name"] == "Some One"
    assert item["user_status"] == "active"
    assert item["email_verified"] is True


async def test_role_holders_disabled_and_unverified_listed(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    disabled = await make_user(db_session)
    disabled.status = UserStatus.DISABLED.value
    unverified = await make_user(db_session)
    await assign(db_session, disabled, role)
    await assign(db_session, unverified, role)
    items = (await rbac_client.get(_url(role.id))).json()["items"]
    by_user = {i["user_id"]: i for i in items}
    assert by_user[str(disabled.id)]["user_status"] == "disabled"
    assert by_user[str(disabled.id)]["active"] is True
    assert by_user[str(unverified.id)]["email_verified"] is False
    assert by_user[str(unverified.id)]["active"] is True


async def test_role_holders_paging_and_order(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(5):
        ra = await assign(db_session, await make_user(db_session), role)
        ra.created_at = base + timedelta(days=i)
        rows.append(ra)
    tied = []
    for _ in range(2):
        ra = await assign(db_session, await make_user(db_session), role)
        ra.created_at = base + timedelta(days=10)
        tied.append(ra)
    await db_session.flush()
    expected = [str(r.id) for r in sorted(tied, key=lambda r: r.id)]
    expected += [str(r.id) for r in reversed(rows)]
    seen: list[str] = []
    for offset in (0, 2, 4, 6):
        resp = await rbac_client.get(_url(role.id), params={"limit": 2, "offset": offset})
        body = resp.json()
        assert body["total"] == 7
        assert (body["limit"], body["offset"]) == (2, offset)
        seen += [i["id"] for i in body["items"]]
    assert seen == expected
    assert len(set(seen)) == 7


async def test_role_holders_limit_over_100_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    for params in ({"limit": 101}, {"offset": -1}):
        resp = await rbac_client.get(_url(role.id), params=params)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


async def _mixed(
    db_session: AsyncSession, role: Role, org_id: uuid.UUID | None = None
) -> list[RoleAssignment]:
    future = datetime.now(UTC) + timedelta(days=30)
    expired = await assign(
        db_session, await make_user(db_session), role, org_id=org_id, expires_at=PAST
    )
    future_row = await assign(
        db_session, await make_user(db_session), role, org_id=org_id, expires_at=future
    )
    forever = await assign(db_session, await make_user(db_session), role, org_id=org_id)
    return [expired, future_row, forever]


async def test_role_holders_expired_rows_default(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    expired, future, forever = await _mixed(db_session, role)
    body = (await rbac_client.get(_url(role.id))).json()
    assert body["total"] == 3
    active = {i["id"]: i["active"] for i in body["items"]}
    assert active == {str(expired.id): False, str(future.id): True, str(forever.id): True}


async def test_role_holders_active_filter(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    expired, future, forever = await _mixed(db_session, role)
    ids, total = await _ids(rbac_client, role, active="true")
    assert (set(ids), total) == ({str(future.id), str(forever.id)}, 2)
    ids, total = await _ids(rbac_client, role, active="false")
    assert (ids, total) == ([str(expired.id)], 1)


async def test_role_holders_active_partition(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    org = await make_org(db_session)
    await _mixed(db_session, role)
    await _mixed(db_session, role, org.id)
    all_ids, _ = await _ids(rbac_client, role)
    on_ids, on_total = await _ids(rbac_client, role, active="true")
    off_ids, off_total = await _ids(rbac_client, role, active="false")
    assert not set(on_ids) & set(off_ids)
    assert set(on_ids) | set(off_ids) == set(all_ids)
    assert on_total + off_total == len(all_ids) == 6
    for flag, value in (("true", True), ("false", False)):
        items = (await rbac_client.get(_url(role.id), params={"active": flag})).json()["items"]
        assert all(i["active"] is value for i in items)


async def test_role_holders_org_filter(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _role(db_session)
    org_a, org_b = await make_org(db_session), await make_org(db_session)
    r1 = await assign(db_session, await make_user(db_session), role)
    r2 = await assign(db_session, await make_user(db_session), role, org_id=org_a.id)
    r3 = await assign(db_session, await make_user(db_session), role, org_id=org_b.id)
    ids, total = await _ids(rbac_client, role, org_id=str(org_a.id))
    assert (ids, total) == ([str(r2.id)], 1)
    items = (await rbac_client.get(_url(role.id))).json()["items"]
    assert {i["id"]: i["org_id"] for i in items} == {
        str(r1.id): None,
        str(r2.id): str(org_a.id),
        str(r3.id): str(org_b.id),
    }


async def test_role_holders_same_user_multiple_rows(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    org = await make_org(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, role)
    await assign(db_session, user, role, org_id=org.id)
    body = (await rbac_client.get(_url(role.id))).json()
    assert body["total"] == 2
    assert {i["user_id"] for i in body["items"]} == {str(user.id)}
    assert len({i["id"] for i in body["items"]}) == 2


async def test_role_holders_org_filter_unknown_org_empty(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    await assign(db_session, await make_user(db_session), role)
    resp = await rbac_client.get(_url(role.id), params={"org_id": str(uuid.uuid4())})
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["total"] == 0


async def test_role_holders_filters_combined(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    org = await make_org(db_session)
    expired = await assign(
        db_session, await make_user(db_session), role, org_id=org.id, expires_at=PAST
    )
    live = await assign(db_session, await make_user(db_session), role, org_id=org.id)
    await assign(db_session, await make_user(db_session), role, expires_at=PAST)
    await assign(db_session, await make_user(db_session), role)
    org_id = str(org.id)
    assert await _ids(rbac_client, role, active="true", org_id=org_id) == ([str(live.id)], 1)
    assert await _ids(rbac_client, role, active="false", org_id=org_id) == ([str(expired.id)], 1)


async def test_role_holders_bad_query_values_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    for params in ({"org_id": "not-a-uuid"}, {"active": "maybe"}):
        resp = await rbac_client.get(_url(role.id), params=params)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


async def test_role_holders_bad_role_uuid_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _role(db_session)
    resp = await rbac_client.get(_url("not-a-uuid"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_role_holders_empty_role(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _role(db_session)
    resp = await rbac_client.get(_url(role.id))
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["total"] == 0


async def test_role_holders_only_this_role(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _role(db_session)
    other = await make_role(db_session, app, "stoktok:editor")
    mine = await assign(db_session, await make_user(db_session), role)
    await assign(db_session, await make_user(db_session), other)
    assert await _ids(rbac_client, role) == ([str(mine.id)], 1)


async def test_role_holders_404_unknown_app_and_malformed_key(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    for key in ("nope", "BAD_KEY!"):
        resp = await rbac_client.get(_url(role.id, key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


async def test_role_holders_404_unknown_role(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, role)
    resp = await rbac_client.get(_url(uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert user.email not in resp.text


async def test_role_holders_404_role_of_other_app(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _role(db_session)
    _, other_role = await _role(db_session, "other")
    user = await make_user(db_session)
    await assign(db_session, user, other_role)
    resp = await rbac_client.get(_url(other_role.id, "stoktok"))
    assert resp.status_code == 404
    assert user.email not in resp.text
    assert (await rbac_client.get(_url(other_role.id, "other"))).status_code == 200


async def test_role_holders_admin_app_leaf_permission(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    role = await super_admin_role(db_session)
    user = await make_user(db_session)
    ra = await assign(db_session, user, role)
    async with make_client(admin_permissions=["admin:users:read"]) as c:
        resp = await c.get(_url(role.id, "admin"), params={"limit": 100})
    assert resp.status_code == 200
    assert str(ra.id) in {i["id"] for i in resp.json()["items"]}


async def test_role_holders_disabled_app_listed(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "oldapp", status="disabled")
    role = await make_role(db_session, app, "oldapp:viewer")
    ra = await assign(db_session, await make_user(db_session), role)
    resp = await rbac_client.get(_url(role.id, "oldapp"))
    assert resp.status_code == 200
    assert [i["id"] for i in resp.json()["items"]] == [str(ra.id)]


async def test_role_holders_403_without_permission(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    _, role = await _role(db_session)
    urls = (_url(role.id), _url(role.id, "nope"), _url(uuid.uuid4()))
    for perms in (["admin:apps:read"], ["admin:roles:assign"]):
        async with make_client(admin_permissions=perms) as c:
            for url in urls:
                resp = await c.get(url)
                assert resp.status_code == 403
                assert resp.json()["error"]["details"]["required"] == "admin:users:read"
    for perms in (["admin:users:read"], ["admin:*"]):
        async with make_client(admin_permissions=perms) as c:
            assert (await c.get(_url(role.id))).status_code == 200


async def test_role_holders_401_before_404_and_422(client: AsyncClient) -> None:
    assert (await client.get(_url(uuid.uuid4(), "nope"))).status_code == 401
    assert (await client.get(_url("not-a-uuid"))).status_code == 401
    assert (await client.get(_url(uuid.uuid4()), params={"active": "maybe"})).status_code == 401


async def test_role_holders_bounded_queries(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, one = await _role(db_session)
    six = await make_role(db_session, app, "stoktok:six")
    await assign(db_session, await make_user(db_session), one)
    for _ in range(6):
        org = await make_org(db_session)
        await assign(db_session, await make_user(db_session), six, org_id=org.id)
    counts = []
    for role, n in ((one, 1), (six, 6)):
        with _capture_sql(db_session) as statements:
            resp = await rbac_client.get(_url(role.id))
        assert len(resp.json()["items"]) == n
        counts.append(len(statements))
    assert counts[0] == counts[1]
