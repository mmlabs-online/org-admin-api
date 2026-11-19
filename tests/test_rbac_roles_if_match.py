"""USR-24: optimistic concurrency (ETag / If-Match) on PUT /apps/{key}/roles/{id}/permissions."""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.etag import version_etag
from app.modules.audit.models import AuditLog
from app.modules.rbac.models import Role
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import RoleCatalogService
from tests.conftest import MakeClient
from tests.rbac_factories import make_app, make_permission, make_role
from tests.test_rbac_roles_api import BASE, _capture_sql, _held, _put_url, _setup

A, B, C = "stoktok:a:read", "stoktok:b:read", "stoktok:c:read"
ADMIN_PERM = "stoktok:roles:manage"


@pytest.fixture
async def rbac_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"]) as c:
        yield c


def _def_url(role_id: uuid.UUID) -> str:
    return f"{BASE}/stoktok/role-definitions/{role_id}"


async def _tag(client: AsyncClient, role: Role) -> str:
    resp = await client.get(_def_url(role.id))
    assert resp.status_code == 200
    return str(resp.headers["etag"])


async def _put(
    client: AsyncClient, role: Role, keys: list[str], tag: str | None = None, app: str = "stoktok"
) -> Response:
    headers = {"If-Match": tag} if tag is not None else {}
    return await client.put(_put_url(role.id, app), json={"permission_keys": keys}, headers=headers)


async def _audit_count(db: AsyncSession) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "role.permissions_replaced")
        )
        or 0
    )


async def _updated_at(db: AsyncSession, role: Role) -> datetime:
    value = await db.scalar(select(Role.updated_at).where(Role.id == role.id))
    assert value is not None
    return value


def _ts(body: dict[str, str]) -> datetime:
    return datetime.fromisoformat(body["updated_at"])


def _assert_412(resp: Response, current_tag: str) -> None:
    assert resp.status_code == 412, resp.text
    err = resp.json()["error"]
    assert err["code"] == "precondition_failed"
    assert err["details"] is None
    assert resp.headers["etag"] == current_tag


async def test_get_role_definition_returns_etag(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    resp = await rbac_client.get(_def_url(role.id))
    assert resp.headers["etag"] == version_etag(_ts(resp.json()))


async def test_put_changing_set_bumps_updated_at(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    before = await rbac_client.get(_def_url(role.id))
    first = await _put(rbac_client, role, [A])
    assert first.status_code == 200
    assert _ts(first.json()) > _ts(before.json())
    again = await rbac_client.get(_def_url(role.id))
    assert _ts(again.json()) == _ts(first.json())
    assert first.headers["etag"] == again.headers["etag"]
    second = await _put(rbac_client, role, [A, B])
    assert _ts(second.json()) > _ts(first.json())


async def test_put_noop_does_not_bump(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    first = await _put(rbac_client, role, [A])
    audits = await _audit_count(db_session)
    for tag in (None, first.headers["etag"]):
        again = await _put(rbac_client, role, [A], tag)
        assert again.status_code == 200
        assert again.json()["updated_at"] == first.json()["updated_at"]
        assert again.headers["etag"] == first.headers["etag"]
    assert await _audit_count(db_session) == audits


async def test_put_matching_if_match_200(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    tag_a = await _tag(rbac_client, role)
    resp = await _put(rbac_client, role, [A], tag_a)
    assert resp.status_code == 200
    assert resp.headers["etag"] != tag_a
    assert await _held(db_session, role.id) == {A}
    assert await _audit_count(db_session) == 1
    assert (await _put(rbac_client, role, [B], resp.headers["etag"])).status_code == 200


async def test_put_stale_if_match_412(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    tag_a = await _tag(rbac_client, role)
    first = await _put(rbac_client, role, [A], tag_a)
    tag_b = first.headers["etag"]
    audits = await _audit_count(db_session)
    stale = await _put(rbac_client, role, [B], tag_a)
    _assert_412(stale, tag_b)
    assert await _held(db_session, role.id) == {A}
    assert await _audit_count(db_session) == audits
    assert version_etag(await _updated_at(db_session, role)) == tag_b


async def test_two_clients_second_gets_412(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    tag = await _tag(rbac_client, role)
    assert (await _put(rbac_client, role, [A], tag)).status_code == 200
    assert (await _put(rbac_client, role, [B], tag)).status_code == 412
    assert await _held(db_session, role.id) == {A}


async def test_put_if_match_list_containing_current_200(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    tag = await _tag(rbac_client, role)
    assert (await _put(rbac_client, role, [A], f'"0", {tag}')).status_code == 200


async def test_put_if_match_star_200(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    assert (await _put(rbac_client, role, [A], "*")).status_code == 200
    assert await _held(db_session, role.id) == {A}


@pytest.mark.parametrize("form", ["weak", "garbage"])
async def test_put_weak_or_malformed_if_match_412(
    rbac_client: AsyncClient, db_session: AsyncSession, form: str
) -> None:
    _, role = await _setup(db_session)
    tag = await _tag(rbac_client, role)
    sent = f"W/{tag}" if form == "weak" else "garbage"
    _assert_412(await _put(rbac_client, role, [A], sent), tag)
    assert await _held(db_session, role.id) == set()


async def test_put_noop_with_stale_if_match_412(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    tag_a = await _tag(rbac_client, role)
    first = await _put(rbac_client, role, [A])
    _assert_412(await _put(rbac_client, role, [A], tag_a), first.headers["etag"])


async def test_put_without_if_match_unchanged(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    await _put(rbac_client, role, [A])
    resp = await _put(rbac_client, role, [B])
    assert resp.status_code == 200
    assert await _held(db_session, role.id) == {B}
    assert await _audit_count(db_session) == 2


async def test_put_unknown_role_404_beats_412(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    stale = '"1"'
    unknown = await rbac_client.put(
        _put_url(uuid.uuid4()), json={"permission_keys": [A]}, headers={"If-Match": stale}
    )
    assert unknown.status_code == 404
    assert (await _put(rbac_client, role, [A], stale, app="nope")).status_code == 404


async def test_put_stale_and_unknown_permission_gives_412(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    resp = await _put(rbac_client, role, ["stoktok:nope:read"], '"1"')
    assert resp.status_code == 412


async def test_put_stale_and_409_rule_gives_412(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _setup(db_session)
    app.default_role_id = role.id
    await db_session.flush()
    resp = await _put(rbac_client, role, [ADMIN_PERM], '"1"')
    assert resp.status_code == 412
    assert (await _put(rbac_client, role, [ADMIN_PERM])).status_code == 409


async def test_put_stale_and_needs_super_gives_412_no_denied_audit(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    async with make_client(admin_permissions=["admin:roles:manage", "admin:apps:read"]) as client:
        tag = await _tag(client, role)
        total_before = await _audit_total(db_session)
        stale = await _put(client, role, [ADMIN_PERM], '"1"')
        assert stale.status_code == 412
        assert await _audit_total(db_session) == total_before
        denied_before = await _audit_total(db_session)
        fresh = await _put(client, role, [ADMIN_PERM], tag)
        assert fresh.status_code == 403
        assert fresh.json()["error"]["code"] == "admin_wildcard_required"
        assert await _audit_total(db_session) == denied_before + 1


async def _audit_total(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(AuditLog)) or 0)


async def test_put_if_match_401_unchanged(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    async with make_client() as client:
        resp = await _put(client, role, [A], '"1"')
    assert resp.status_code in (401, 403)


async def test_put_if_match_403_unchanged(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    async with make_client(admin_permissions=["admin:apps:read"]) as client:
        resp = await _put(client, role, [A], '"1"')
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] != "precondition_failed"


async def test_precondition_read_after_app_lock(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    with _capture_sql(db_session) as sql:
        resp = await _put(rbac_client, role, [A], '"1"')
    assert resp.status_code == 412
    lock = next(i for i, s in enumerate(sql) if "FOR NO KEY UPDATE" in s)
    roles = next(i for i, s in enumerate(sql) if "FROM roles" in s and "FOR NO KEY" not in s)
    assert lock < roles
    assert not [s for s in sql if s.lstrip().upper().startswith(("INSERT", "DELETE", "UPDATE"))]


async def test_rename_bumps_updated_at(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    put = await _put(rbac_client, role, [A])
    url = f"{BASE}/stoktok/roles/{role.id}"
    renamed = await rbac_client.patch(url, json={"name": "Renamed"})
    assert renamed.status_code == 200
    assert _ts(renamed.json()) > _ts(put.json())
    for body in ({}, {"name": "Renamed"}):
        same = await rbac_client.patch(url, json=body)
        assert same.json()["updated_at"] == renamed.json()["updated_at"]


async def test_default_role_rebuild_bumps_member_version(db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    await make_permission(db_session, app, A)
    service = RoleCatalogService(db_session)
    await service.build_default_role("stoktok")
    member = await RbacRepository(db_session).get_role_by_key(app.id, "stoktok:member")
    assert member is not None
    first = await _updated_at(db_session, member)
    await make_permission(db_session, app, B)
    await service.build_default_role("stoktok")
    second = await _updated_at(db_session, member)
    assert second > first
    await service.build_default_role("stoktok")
    assert await _updated_at(db_session, member) == second


async def test_touch_role_strictly_increasing(db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:viewer")
    repo = RbacRepository(db_session)
    await repo.touch_role(role)
    first = role.updated_at
    await repo.touch_role(role)
    assert role.updated_at > first


async def test_get_role_populates_existing(db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:viewer")
    repo = RbacRepository(db_session)
    await db_session.refresh(role)
    new = role.updated_at.replace(year=2001)
    await db_session.execute(
        update(Role)
        .where(Role.id == role.id)
        .values(updated_at=new)
        .execution_options(synchronize_session=False)
    )
    fresh = await repo.get_role(app.id, role.id)
    assert fresh is not None and fresh.updated_at == new
