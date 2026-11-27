"""USR-25: nobody grants, renews or revokes their own privileged role in any app (ADR-0008).

Privileged = an admin-app role, a role holding an `is_admin` permission (either scope) or the
app's designated super-admin role. Non-privileged roles stay self-assignable.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.rbac.actor import (
    SYSTEM_CLI,
    SYSTEM_JOIN,
    SYSTEM_ORG_CREATE,
    SYSTEM_ORG_INVITE,
)
from app.modules.rbac.models import App, Permission, Role, RoleAssignment
from app.modules.rbac.service import AssignmentService
from app.modules.users.models import User
from tests.auth_factories import make_login_user
from tests.conftest import MakeClient
from tests.join_request_factories import make_approval_app, make_join_request
from tests.rbac_factories import (
    assign,
    grant,
    make_app,
    make_org,
    make_permission,
    make_role,
)

V1 = "/api/v1"
SUPER = ["admin:*"]
LEAF = ["admin:roles:assign"]
MESSAGE = "Cannot change your own privileged roles"


def _url(user_id: uuid.UUID, assignment_id: uuid.UUID | None = None) -> str:
    base = f"{V1}/users/{user_id}/role-assignments"
    return base if assignment_id is None else f"{base}/{assignment_id}"


def _error(resp: Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    error: dict[str, Any] = resp.json()["error"]
    assert error["code"] == code, resp.text
    return error


async def _privileged_role(
    db: AsyncSession, *, scope: str = "app", **app_kwargs: object
) -> tuple[App, Role]:
    app = await make_app(db, "stoktok", **app_kwargs)
    perm = await make_permission(db, app, "stoktok:*", is_admin=True, scope=scope)
    role = await make_role(db, app, "stoktok:admin")
    await grant(db, role, perm)
    return app, role


async def _rows(db: AsyncSession, user: User | uuid.UUID) -> list[RoleAssignment]:
    rows = await db.scalars(
        select(RoleAssignment)
        .where(RoleAssignment.user_id == (user.id if isinstance(user, User) else user))
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _denied(db: AsyncSession, actor: uuid.UUID) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.outcome == "denied", AuditLog.actor_user_id == actor)
        .execution_options(populate_existing=True)
    )
    return list(rows)


@pytest.mark.parametrize("perm_scope", ["app", "org"])
async def test_self_grant_product_admin_role_403(
    make_client: MakeClient, db_session: AsyncSession, perm_scope: str
) -> None:
    me = await make_login_user(db_session)
    other = await make_login_user(db_session)
    _, role = await _privileged_role(
        db_session, scope=perm_scope, org_mode="required" if perm_scope == "org" else "none"
    )
    me_id, other_id, role_id = me.id, other.id, role.id
    await db_session.commit()
    body = {"role_id": str(role_id)}
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        error = _error(await client.post(_url(me_id), json=body), 403, "self_assignment_forbidden")
        assert error["details"] is None
        assert error["message"] == MESSAGE
        assert (await client.post(_url(other_id), json=body)).status_code == 201
    assert await _rows(db_session, me_id) == []
    (row,) = await _denied(db_session, me_id)
    assert row.action == "role_assignment.granted"
    assert row.changes == {"reason": "self_assignment_forbidden", "role_key": "stoktok:admin"}


async def test_self_grant_product_admin_role_without_wildcard_403_wildcard(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await make_login_user(db_session)
    _, role = await _privileged_role(db_session)
    me_id, role_id = me.id, role.id
    await db_session.commit()
    async with make_client(admin_permissions=LEAF, admin_user=me) as client:
        resp = await client.post(_url(me_id), json={"role_id": str(role_id)})
    _error(resp, 403, "admin_wildcard_required")
    (row,) = await _denied(db_session, me_id)
    assert row.changes is not None and row.changes["reason"] == "admin_wildcard_required"


async def test_self_renew_expired_product_admin_role_403(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await make_login_user(db_session)
    _, role = await _privileged_role(db_session)
    expired = datetime.now(UTC) - timedelta(days=1)
    ra = await assign(db_session, me, role, expires_at=expired)
    me_id, ra_id, role_id = me.id, ra.id, role.id
    await db_session.commit()
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        resp = await client.post(_url(me_id), json={"role_id": str(role_id)})
    _error(resp, 403, "self_assignment_forbidden")
    (row,) = await _rows(db_session, me_id)
    assert (row.id, row.expires_at, row.granted_by) == (ra_id, expired, None)


async def test_self_revoke_product_admin_role_403(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await make_login_user(db_session)
    peer = await make_login_user(db_session)
    _, role = await _privileged_role(db_session)
    ra = await assign(db_session, me, role)
    me_id, ra_id = me.id, ra.id
    await db_session.commit()
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        resp = await client.delete(_url(me_id, ra_id))
    _error(resp, 403, "self_assignment_forbidden")
    assert len(await _rows(db_session, me_id)) == 1
    (row,) = await _denied(db_session, me_id)
    assert (row.action, row.target_id) == ("role_assignment.revoked", ra_id)
    await db_session.refresh(peer)  # expired by the denied request's rollback
    async with make_client(admin_permissions=SUPER, admin_user=peer) as client:
        assert (await client.delete(_url(me_id, ra_id))).status_code == 204


async def test_self_grant_and_revoke_member_role_ok(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await make_login_user(db_session)
    app = await make_app(db_session, "stoktok")
    plain = await make_permission(db_session, app, "stoktok:a:read")
    with_perm = await make_role(db_session, app, "stoktok:member")
    await grant(db_session, with_perm, plain)
    without = await make_role(db_session, app, "stoktok:empty")
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        for role in (with_perm, without):
            resp = await client.post(_url(me.id), json={"role_id": str(role.id)})
            assert resp.status_code == 201, resp.text
            assert resp.json()["granted_by"] == str(me.id)
            deleted = await client.delete(_url(me.id, resp.json()["id"]))
            assert deleted.status_code == 204, deleted.text


async def test_self_grant_org_scoped_privileged_role_403(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await make_login_user(db_session)
    other = await make_login_user(db_session)
    _, role = await _privileged_role(db_session, scope="org", org_mode="required")
    org = await make_org(db_session)
    me_id, other_id, org_id, role_id = me.id, other.id, org.id, role.id
    await db_session.commit()
    body = {"role_id": str(role_id), "org_id": str(org_id)}
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        _error(await client.post(_url(me_id), json=body), 403, "self_assignment_forbidden")
        assert (await client.post(_url(other_id), json=body)).status_code == 201
    (row,) = await _denied(db_session, me_id)
    assert row.org_id == org_id


async def test_self_revoke_role_after_is_admin_removed_ok(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await make_login_user(db_session)
    app, role = await _privileged_role(db_session)
    await make_permission(db_session, app, "stoktok:a:read")
    ra = await assign(db_session, me, role)
    await db_session.commit()
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        put = await client.put(
            f"{V1}/apps/stoktok/roles/{role.id}/permissions",
            json={"permission_keys": ["stoktok:a:read"]},
        )
        assert put.status_code == 200, put.text
        assert (await client.delete(_url(me.id, ra.id))).status_code == 204


async def test_g3_chain_strip_self_assign_readd_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await make_login_user(db_session)
    _, role = await _privileged_role(db_session)
    await db_session.commit()
    put_url = f"{V1}/apps/stoktok/roles/{role.id}/permissions"
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        assert (await client.put(put_url, json={"permission_keys": []})).status_code == 200
        assigned = await client.post(_url(me.id), json={"role_id": str(role.id)})
        assert assigned.status_code == 201, assigned.text
        error = _error(
            await client.put(put_url, json={"permission_keys": ["stoktok:*"]}),
            409,
            "role_has_assignments",
        )
    assert error["details"] == [{"field": "permission_keys", "reason": "role_has_assignments"}]


async def test_g3_chain_flip_is_admin_after_self_assign_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await make_login_user(db_session)
    app = await make_app(db_session, "stoktok")
    perm: Permission = await make_permission(db_session, app, "stoktok:a:write")
    role = await make_role(db_session, app, "stoktok:editor")
    await grant(db_session, role, perm)
    await db_session.commit()
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        assigned = await client.post(_url(me.id), json={"role_id": str(role.id)})
        assert assigned.status_code == 201, assigned.text
        error = _error(
            await client.patch(f"{V1}/apps/stoktok/permissions/{perm.id}", json={"is_admin": True}),
            409,
            "role_has_assignments",
        )
    assert error["details"] == [{"field": "is_admin", "reason": "role_has_assignments"}]


async def test_join_approve_own_request_allowed(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    staff = await make_login_user(db_session)
    for perms in (["admin:users:read", "admin:roles:assign"], SUPER):
        app = await make_approval_app(db_session)
        row = await make_join_request(db_session, app, staff)
        async with make_client(admin_permissions=perms, admin_user=staff) as client:
            resp = await client.post(f"{V1}/apps/{app.key}/join-requests/{row.id}/approve")
        assert resp.status_code == 200, resp.text
    held = await _rows(db_session, staff)
    assert len(held) == 2 and all(ra.granted_by == staff.id for ra in held)


async def test_forbid_self_system_actor_never_refuses(db_session: AsyncSession) -> None:
    service = AssignmentService(db_session)
    for actor in (SYSTEM_CLI, SYSTEM_JOIN, SYSTEM_ORG_CREATE, SYSTEM_ORG_INVITE):
        service._forbid_self(True, uuid.uuid4(), actor)
        service._forbid_self(False, uuid.uuid4(), actor)
