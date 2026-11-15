"""ADM-002b: `admin:*` guards on the users/RBAC admin APIs (ADR-0008 amendment).

"Leaf" = the principal holds only the route's own permission; "super" = `admin:*`. A refused
request must leave every row as it was (`_state` snapshots the RBAC, user and session tables).
"""

import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import Response
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.repository import EmailTokenRepository
from app.modules.rbac.models import App, Permission, Role
from app.modules.rbac.repository import APPS_CHANGED_KEY
from app.modules.users.models import User
from tests.auth_factories import (
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    make_member_role,
    sha256,
)
from tests.conftest import MakeClient
from tests.rbac_factories import (
    assign,
    grant,
    make_app,
    make_permission,
    make_role,
    super_admin_role,
)

V1 = "/api/v1"
SUPER = ["admin:*"]
ORIGIN_STOKTOK = "https://stoktok.mmlabs.online"
ORIGIN_ADMIN = "https://admin.mmlabs.online"

_STATE_SQL = {
    "apps": "SELECT key, name, status, join_policy, origins, default_role_id, updated_at "
    "FROM apps ORDER BY key",
    "permissions": "SELECT app_id, key, description, is_admin, updated_at "
    "FROM permissions ORDER BY app_id, key",
    "roles": "SELECT app_id, key, name, updated_at FROM roles ORDER BY app_id, key",
    "role_permissions": "SELECT role_id, permission_id FROM role_permissions "
    "ORDER BY role_id, permission_id",
    "role_assignments": "SELECT id, user_id, role_id, org_id, granted_by, expires_at "
    "FROM role_assignments ORDER BY id",
    "users": "SELECT id, status, updated_at FROM users ORDER BY id",
    "account_sessions": "SELECT id, revoked_at FROM account_sessions ORDER BY id",
}


async def _state(db: AsyncSession) -> dict[str, list[tuple[Any, ...]]]:
    return {
        name: [tuple(row) for row in (await db.execute(text(sql))).all()]
        for name, sql in _STATE_SQL.items()
    }


def _error(resp: Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    error: dict[str, Any] = resp.json()["error"]
    assert error["code"] == code, resp.text
    return error


def _wildcard_required(resp: Response) -> None:
    assert _error(resp, 403, "admin_wildcard_required")["details"] == {"required": "admin:*"}


async def _admin_app(db: AsyncSession) -> App:
    app = await db.scalar(select(App).where(App.key == "admin"))
    assert app is not None
    return app


async def _admin_perm(db: AsyncSession, key: str) -> Permission:
    perm = await db.scalar(select(Permission).where(Permission.key == key))
    assert perm is not None
    return perm


async def _verified_user(db: AsyncSession, status: str = "active") -> User:
    return await make_login_user(db, status=status)


async def _admin_role(db: AsyncSession, key: str = "admin:auditor") -> Role:
    """An admin-app role holding `admin:users:read` (a privileged role by app)."""
    role = await make_role(db, await _admin_app(db), key)
    await grant(db, role, await _admin_perm(db, "admin:users:read"))
    return role


async def _stoktok_admin_role(db: AsyncSession) -> tuple[App, Role, Permission]:
    """`stoktok:admin` holding `stoktok:*` (a privileged role by `is_admin`)."""
    app = await make_app(db, "stoktok", origins=[ORIGIN_STOKTOK])
    wildcard = await make_permission(db, app, "stoktok:*", is_admin=True)
    role = await make_role(db, app, "stoktok:admin")
    await grant(db, role, wildcard)
    return app, role, wildcard


def _assignments(user_id: uuid.UUID, assignment_id: uuid.UUID | None = None) -> str:
    base = f"{V1}/users/{user_id}/role-assignments"
    return base if assignment_id is None else f"{base}/{assignment_id}"


# --- guard matrix (Approach §3) -------------------------------------------------------


@dataclass(frozen=True)
class Case:
    method: str
    url: str
    body: dict[str, Any] | None
    leaf: str  # the route's own permission
    ok: int  # status for a super-admin


Setup = Callable[[AsyncSession], Awaitable[Case]]


async def _app_admin_patch(db: AsyncSession) -> Case:
    return Case("PATCH", f"{V1}/apps/admin", {"name": "Admin 2"}, "admin:apps:write", 200)


async def _app_admin_origins_patch(db: AsyncSession) -> Case:
    body = {"origins": [ORIGIN_ADMIN]}
    return Case("PATCH", f"{V1}/apps/admin", body, "admin:apps:write", 200)


async def _permission_create_admin_app(db: AsyncSession) -> Case:
    body = {"key": "admin:widgets:read"}
    return Case("POST", f"{V1}/apps/admin/permissions", body, "admin:roles:manage", 201)


async def _permission_create_is_admin(db: AsyncSession) -> Case:
    await make_app(db, "stoktok")
    body = {"key": "stoktok:x:write", "is_admin": True}
    return Case("POST", f"{V1}/apps/stoktok/permissions", body, "admin:roles:manage", 201)


async def _permission_create_wildcard(db: AsyncSession) -> Case:
    await make_app(db, "stoktok")
    body = {"key": "stoktok:*"}
    return Case("POST", f"{V1}/apps/stoktok/permissions", body, "admin:roles:manage", 201)


async def _permission_update_admin_app(db: AsyncSession) -> Case:
    perm = await _admin_perm(db, "admin:users:read")
    url = f"{V1}/apps/admin/permissions/{perm.id}"
    return Case("PATCH", url, {"description": "changed"}, "admin:roles:manage", 200)


async def _permission_is_admin_false_to_true(db: AsyncSession) -> Case:
    app = await make_app(db, "stoktok")
    perm = await make_permission(db, app, "stoktok:a:write")
    url = f"{V1}/apps/stoktok/permissions/{perm.id}"
    return Case("PATCH", url, {"is_admin": True}, "admin:roles:manage", 200)


async def _permission_is_admin_true_to_false(db: AsyncSession) -> Case:
    app = await make_app(db, "stoktok")
    perm = await make_permission(db, app, "stoktok:a:write", is_admin=True)
    url = f"{V1}/apps/stoktok/permissions/{perm.id}"
    return Case("PATCH", url, {"is_admin": False}, "admin:roles:manage", 200)


async def _permission_delete_admin_app(db: AsyncSession) -> Case:
    perm = await make_permission(db, await _admin_app(db), "admin:widgets:read", is_admin=True)
    url = f"{V1}/apps/admin/permissions/{perm.id}"
    return Case("DELETE", url, None, "admin:roles:manage", 204)


async def _role_create_admin_app(db: AsyncSession) -> Case:
    body = {"key": "admin:auditor", "name": "Auditor"}
    return Case("POST", f"{V1}/apps/admin/roles", body, "admin:roles:manage", 201)


async def _role_rename_admin_app(db: AsyncSession) -> Case:
    role = await _admin_role(db)
    url = f"{V1}/apps/admin/roles/{role.id}"
    return Case("PATCH", url, {"name": "Auditor 2"}, "admin:roles:manage", 200)


async def _role_delete_admin_app(db: AsyncSession) -> Case:
    role = await _admin_role(db)
    return Case("DELETE", f"{V1}/apps/admin/roles/{role.id}", None, "admin:roles:manage", 204)


async def _role_put_admin_app(db: AsyncSession) -> Case:
    role = await _admin_role(db)
    url = f"{V1}/apps/admin/roles/{role.id}/permissions"
    body = {"permission_keys": ["admin:users:read", "admin:apps:read"]}
    return Case("PUT", url, body, "admin:roles:manage", 200)


async def _role_put_adds_is_admin(db: AsyncSession) -> Case:
    app = await make_app(db, "stoktok")
    await make_permission(db, app, "stoktok:a:write", is_admin=True)
    role = await make_role(db, app, "stoktok:editor")
    url = f"{V1}/apps/stoktok/roles/{role.id}/permissions"
    return Case("PUT", url, {"permission_keys": ["stoktok:a:write"]}, "admin:roles:manage", 200)


async def _role_put_removes_is_admin(db: AsyncSession) -> Case:
    app = await make_app(db, "stoktok")
    perm = await make_permission(db, app, "stoktok:a:write", is_admin=True)
    role = await make_role(db, app, "stoktok:editor")
    await grant(db, role, perm)
    url = f"{V1}/apps/stoktok/roles/{role.id}/permissions"
    return Case("PUT", url, {"permission_keys": []}, "admin:roles:manage", 200)


async def _grant_super_admin(db: AsyncSession) -> Case:
    target = await _verified_user(db)
    body = {"role_id": str((await super_admin_role(db)).id)}
    return Case("POST", _assignments(target.id), body, "admin:roles:assign", 201)


async def _grant_admin_app_role(db: AsyncSession) -> Case:
    target = await _verified_user(db)
    body = {"role_id": str((await _admin_role(db)).id)}
    return Case("POST", _assignments(target.id), body, "admin:roles:assign", 201)


async def _grant_is_admin_role(db: AsyncSession) -> Case:
    target = await _verified_user(db)
    _, role, _ = await _stoktok_admin_role(db)
    return Case(
        "POST", _assignments(target.id), {"role_id": str(role.id)}, "admin:roles:assign", 201
    )


async def _revoke_admin_app_role(db: AsyncSession) -> Case:
    target = await _verified_user(db)
    ra = await assign(db, target, await _admin_role(db))
    return Case("DELETE", _assignments(target.id, ra.id), None, "admin:roles:assign", 204)


async def _revoke_is_admin_role(db: AsyncSession) -> Case:
    target = await _verified_user(db)
    _, role, _ = await _stoktok_admin_role(db)
    ra = await assign(db, target, role)
    return Case("DELETE", _assignments(target.id, ra.id), None, "admin:roles:assign", 204)


async def _disable_privileged_user(db: AsyncSession) -> Case:
    target = await _verified_user(db)
    await assign(db, target, await _admin_role(db))
    url = f"{V1}/users/{target.id}"
    return Case("PATCH", url, {"status": "disabled"}, "admin:users:write", 200)


async def _reenable_disabled_staff(db: AsyncSession) -> Case:
    target = await _verified_user(db, status="disabled")
    _, role, _ = await _stoktok_admin_role(db)
    await assign(db, target, role)
    url = f"{V1}/users/{target.id}"
    return Case("PATCH", url, {"status": "active"}, "admin:users:write", 200)


async def _reenable_expired_staff(db: AsyncSession) -> Case:
    """Expired rows count too: they can be renewed (A3)."""
    target = await _verified_user(db, status="disabled")
    await assign(db, target, await _admin_role(db), expires_at=datetime.now(UTC) - timedelta(1))
    url = f"{V1}/users/{target.id}"
    return Case("PATCH", url, {"status": "active"}, "admin:users:write", 200)


CASES: dict[str, Setup] = {
    "app_admin_patch": _app_admin_patch,
    "app_admin_origins_patch": _app_admin_origins_patch,
    "permission_create_admin_app": _permission_create_admin_app,
    "permission_create_is_admin": _permission_create_is_admin,
    "permission_create_wildcard": _permission_create_wildcard,
    "permission_update_admin_app": _permission_update_admin_app,
    "permission_is_admin_false_to_true": _permission_is_admin_false_to_true,
    "permission_is_admin_true_to_false": _permission_is_admin_true_to_false,
    "permission_delete_admin_app": _permission_delete_admin_app,
    "role_create_admin_app": _role_create_admin_app,
    "role_rename_admin_app": _role_rename_admin_app,
    "role_delete_admin_app": _role_delete_admin_app,
    "role_put_admin_app": _role_put_admin_app,
    "role_put_adds_is_admin": _role_put_adds_is_admin,
    "role_put_removes_is_admin": _role_put_removes_is_admin,
    "grant_super_admin": _grant_super_admin,
    "grant_admin_app_role": _grant_admin_app_role,
    "grant_is_admin_role": _grant_is_admin_role,
    "revoke_admin_app_role": _revoke_admin_app_role,
    "revoke_is_admin_role": _revoke_is_admin_role,
    "disable_privileged_user": _disable_privileged_user,
    "reenable_disabled_staff": _reenable_disabled_staff,
    "reenable_expired_staff": _reenable_expired_staff,
}


@pytest.mark.parametrize("who", ["leaf", "super"])
@pytest.mark.parametrize("site", list(CASES))
async def test_guard_matrix(
    make_client: MakeClient, db_session: AsyncSession, site: str, who: str
) -> None:
    case = await CASES[site](db_session)
    perms = [case.leaf] if who == "leaf" else SUPER
    actor = await _verified_user(db_session)  # a real row: `granted_by` references users
    before = await _state(db_session)
    async with make_client(admin_permissions=perms, admin_user=actor) as client:
        resp = await client.request(case.method, case.url, json=case.body)
    if who == "leaf":
        _wildcard_required(resp)
        assert await _state(db_session) == before
    else:
        assert resp.status_code == case.ok, resp.text


# --- no over-blocking ---------------------------------------------------------------


async def test_leaf_allowed_on_ordinary_objects(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:read")
    target = await _verified_user(db_session)
    actor = await _verified_user(db_session)
    leaves = ["admin:roles:manage", "admin:roles:assign", "admin:apps:write", "admin:users:write"]
    async with make_client(admin_permissions=leaves, admin_user=actor) as client:
        resp = await client.post(f"{V1}/apps/stoktok/permissions", json={"key": "stoktok:b:read"})
        assert resp.status_code == 201, resp.text
        resp = await client.patch(
            f"{V1}/apps/stoktok/permissions/{perm.id}", json={"description": "d"}
        )
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"{V1}/apps/stoktok/roles", json={"key": "stoktok:viewer", "name": "Viewer"}
        )
        assert resp.status_code == 201, resp.text
        role_id = resp.json()["id"]
        resp = await client.put(
            f"{V1}/apps/stoktok/roles/{role_id}/permissions",
            json={"permission_keys": ["stoktok:a:read", "stoktok:b:read"]},
        )
        assert resp.status_code == 200, resp.text
        resp = await client.post(_assignments(target.id), json={"role_id": role_id})
        assert resp.status_code == 201, resp.text
        resp = await client.delete(_assignments(target.id, resp.json()["id"]))
        assert resp.status_code == 204, resp.text
        resp = await client.patch(f"{V1}/apps/stoktok", json={"name": "Stoktok 2"})
        assert resp.status_code == 200, resp.text
        resp = await client.patch(f"{V1}/users/{target.id}", json={"status": "disabled"})
        assert resp.status_code == 200, resp.text


async def test_role_put_unchanged_super_admin_ok(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    role = await super_admin_role(db_session)
    before = await _state(db_session)
    async with make_client(admin_permissions=["admin:roles:manage"]) as client:
        resp = await client.put(
            f"{V1}/apps/admin/roles/{role.id}/permissions", json={"permission_keys": ["admin:*"]}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["permissions"] == ["admin:*"]
    assert await _state(db_session) == before


async def test_permission_description_only_leaf_ok(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:write", is_admin=True)
    async with make_client(admin_permissions=["admin:roles:manage"]) as client:
        resp = await client.patch(
            f"{V1}/apps/stoktok/permissions/{perm.id}",
            json={"description": "Write A", "is_admin": True},  # is_admin sent, unchanged
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "Write A"


async def test_admin_app_same_value_patch_leaf_ok(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    admin = await _admin_app(db_session)
    past = datetime(2020, 1, 1, tzinfo=UTC)
    await db_session.execute(update(App).where(App.id == admin.id).values(updated_at=past))
    async with make_client(admin_permissions=["admin:apps:write"]) as client:
        resp = await client.patch(
            f"{V1}/apps/admin", json={"name": admin.name, "join_policy": "closed"}
        )
    assert resp.status_code == 200, resp.text
    assert datetime.fromisoformat(resp.json()["updated_at"]) == past


# --- role_has_assignments (G3, USR-02d) and the wildcard rule ------------------------


async def test_is_admin_flip_on_assigned_role_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:write")
    role = await make_role(db_session, app, "stoktok:editor")
    await grant(db_session, role, perm)
    await assign(db_session, await _verified_user(db_session), role)
    before = await _state(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(
            f"{V1}/apps/stoktok/permissions/{perm.id}", json={"is_admin": True}
        )
    error = _error(resp, 409, "role_has_assignments")
    assert error["details"] == [{"field": "is_admin", "reason": "role_has_assignments"}]
    assert await _state(db_session) == before


async def test_is_admin_onto_assigned_role_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    await make_permission(db_session, app, "stoktok:a:write", is_admin=True)
    assigned = await make_role(db_session, app, "stoktok:editor")
    unassigned = await make_role(db_session, app, "stoktok:other")
    await assign(db_session, await _verified_user(db_session), assigned)
    body = {"permission_keys": ["stoktok:a:write"]}
    before = await _state(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.put(f"{V1}/apps/stoktok/roles/{assigned.id}/permissions", json=body)
        error = _error(resp, 409, "role_has_assignments")
        assert error["details"] == [{"field": "permission_keys", "reason": "role_has_assignments"}]
        assert await _state(db_session) == before
        resp = await client.put(f"{V1}/apps/stoktok/roles/{unassigned.id}/permissions", json=body)
        assert resp.status_code == 200, resp.text


async def test_wildcard_only_on_super_admin_422(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    role = await _admin_role(db_session)
    before = await _state(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.put(
            f"{V1}/apps/admin/roles/{role.id}/permissions", json={"permission_keys": ["admin:*"]}
        )
    error = _error(resp, 422, "unprocessable")
    assert error["details"] == [{"field": "permission_keys", "reason": "wildcard_super_admin_only"}]
    assert await _state(db_session) == before


# --- grants and revokes ---------------------------------------------------------------


async def test_grant_renewal_branch_guarded(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await _verified_user(db_session)
    target = await _verified_user(db_session)
    role = await _admin_role(db_session)
    ra = await assign(db_session, target, role, expires_at=datetime.now(UTC) - timedelta(days=1))
    body = {"role_id": str(role.id)}
    before = await _state(db_session)
    async with make_client(admin_permissions=["admin:roles:assign"], admin_user=actor) as client:
        _wildcard_required(await client.post(_assignments(target.id), json=body))
    assert await _state(db_session) == before

    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_assignments(target.id), json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == str(ra.id)
    assert resp.json()["granted_by"] == str(actor.id)
    await db_session.refresh(ra)
    assert (ra.granted_by, ra.expires_at) == (actor.id, None)


async def test_granted_by_is_actor(make_client: MakeClient, db_session: AsyncSession) -> None:
    actor = await _verified_user(db_session)
    target = await _verified_user(db_session)
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:viewer")
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_assignments(target.id), json={"role_id": str(role.id)})
    assert resp.status_code == 201, resp.text
    assert resp.json()["granted_by"] == str(actor.id)
    granted_by = await db_session.scalar(
        text("SELECT granted_by FROM role_assignments WHERE id = :id"), {"id": resp.json()["id"]}
    )
    assert granted_by == actor.id


@pytest.mark.parametrize("role_kind", ["super_admin", "admin_app_leaf", "app_wildcard"])
async def test_sock_puppet_grant_refused(
    make_client: MakeClient, db_session: AsyncSession, role_kind: str
) -> None:
    actor = await _verified_user(db_session)
    puppet = await _verified_user(db_session)
    if role_kind == "super_admin":
        role = await super_admin_role(db_session)
    elif role_kind == "admin_app_leaf":
        role = await _admin_role(db_session)
    else:
        _, role, _ = await _stoktok_admin_role(db_session)
    before = await _state(db_session)
    async with make_client(admin_permissions=["admin:roles:assign"], admin_user=actor) as client:
        _wildcard_required(
            await client.post(_assignments(puppet.id), json={"role_id": str(role.id)})
        )
    assert await _state(db_session) == before


async def test_self_grant_admin_role_403(make_client: MakeClient, db_session: AsyncSession) -> None:
    me = await _verified_user(db_session)
    role = await _admin_role(db_session)
    body = {"role_id": str(role.id)}
    before = await _state(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        error = _error(
            await client.post(_assignments(me.id), json=body), 403, "self_assignment_forbidden"
        )
        assert error["details"] is None
    async with make_client(admin_permissions=["admin:roles:assign"], admin_user=me) as client:
        _wildcard_required(await client.post(_assignments(me.id), json=body))
    assert await _state(db_session) == before


async def test_self_grant_non_admin_app_role_ok(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await _verified_user(db_session)
    _, role, _ = await _stoktok_admin_role(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        resp = await client.post(_assignments(me.id), json={"role_id": str(role.id)})
    assert resp.status_code == 201, resp.text
    assert resp.json()["granted_by"] == str(me.id)


async def test_self_revoke_admin_role_403(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    me = await _verified_user(db_session)
    ra = await assign(db_session, me, await _admin_role(db_session))
    before = await _state(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        resp = await client.delete(_assignments(me.id, ra.id))
    _error(resp, 403, "self_assignment_forbidden")
    assert await _state(db_session) == before


# --- last super-admin (verified, active, app scope) -----------------------------------


async def test_last_super_admin_revoke_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    only = await _verified_user(db_session)
    ra = await assign(db_session, only, await super_admin_role(db_session))
    before = await _state(db_session)
    async with make_client(admin_permissions=SUPER) as client:  # actor: not a counting holder
        resp = await client.delete(_assignments(only.id, ra.id))
    _error(resp, 409, "last_super_admin")
    assert await _state(db_session) == before


@pytest.mark.parametrize("action", ["revoke", "disable"])
async def test_unverified_super_admin_not_counted(
    make_client: MakeClient, db_session: AsyncSession, action: str
) -> None:
    verified = await _verified_user(db_session)
    unverified = await make_login_user(db_session, verified=False)
    super_admin = await super_admin_role(db_session)
    ra = await assign(db_session, verified, super_admin)
    await assign(db_session, unverified, super_admin)
    before = await _state(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        if action == "revoke":
            resp = await client.delete(_assignments(verified.id, ra.id))
        else:
            resp = await client.patch(f"{V1}/users/{verified.id}", json={"status": "disabled"})
    _error(resp, 409, "last_super_admin")
    assert await _state(db_session) == before


async def test_last_super_admin_disable_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    only = await _verified_user(db_session)
    await assign(db_session, only, await super_admin_role(db_session))
    before = await _state(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(f"{V1}/users/{only.id}", json={"status": "disabled"})
    _error(resp, 409, "last_super_admin")
    assert await _state(db_session) == before


async def test_disable_one_of_two_super_admins_ok(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    first = await _verified_user(db_session)
    second = await _verified_user(db_session)
    super_admin = await super_admin_role(db_session)
    await assign(db_session, first, super_admin)
    await assign(db_session, second, super_admin)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(f"{V1}/users/{first.id}", json={"status": "disabled"})
        assert resp.status_code == 200, resp.text
        resp = await client.patch(f"{V1}/users/{second.id}", json={"status": "disabled"})
        _error(resp, 409, "last_super_admin")


# --- admin app and origins (G1, G6) -----------------------------------------------


async def test_admin_origins_patch_super_validated(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok", origins=[ORIGIN_STOKTOK])
    url = f"{V1}/apps/admin"
    async with make_client(admin_permissions=SUPER) as client:
        error = _error(
            await client.patch(url, json={"origins": ["https://mmlabs.online"]}),
            422,
            "unprocessable",
        )
        assert error["details"] == [{"field": "origins", "reason": "apex_not_allowed"}]
        three = [f"https://a{i}.mmlabs.online" for i in range(3)]
        error = _error(await client.patch(url, json={"origins": three}), 422, "unprocessable")
        assert error["details"] == [{"field": "origins", "reason": "too_many_origins"}]
        error = _error(await client.patch(url, json={"origins": [ORIGIN_STOKTOK]}), 409, "conflict")
        assert error["details"] == [{"field": "origins", "reason": "origin_taken"}]
        assert (await _admin_app(db_session)).origins == []
        assert APPS_CHANGED_KEY not in db_session.info

        resp = await client.patch(url, json={"origins": [ORIGIN_ADMIN]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["origins"] == [ORIGIN_ADMIN]
    assert db_session.info[APPS_CHANGED_KEY] is True


async def test_admin_origins_patch_loopback_refused_in_production(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    async with make_client(admin_permissions=SUPER, **_PRODUCTION) as client:
        resp = await client.patch(f"{V1}/apps/admin", json={"origins": ["http://localhost:3000"]})
    error = _error(resp, 422, "unprocessable")
    assert error["details"] == [{"field": "origins", "reason": "loopback_not_allowed"}]


_PRODUCTION: dict[str, Any] = {
    "app_env": "production",
    "trusted_proxy_cidrs": ["172.18.0.0/16"],
    "argon2_memory_kib": 65536,
    "argon2_time_cost": 3,
}


@pytest.mark.parametrize("holder_status", ["active", "disabled"])
async def test_app_create_origin_taken_409(
    make_client: MakeClient, db_session: AsyncSession, holder_status: str
) -> None:
    await make_app(db_session, "stoktok", origins=[ORIGIN_STOKTOK], status=holder_status)
    before = await _state(db_session)
    async with make_client(admin_permissions=["admin:apps:write"]) as client:
        resp = await client.post(
            f"{V1}/apps", json={"key": "other", "name": "Other", "origins": [ORIGIN_STOKTOK]}
        )
    error = _error(resp, 409, "conflict")
    assert error["details"] == [{"field": "origins", "reason": "origin_taken"}]
    assert await _state(db_session) == before


async def test_app_create_admin_origin_taken_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await db_session.execute(update(App).where(App.key == "admin").values(origins=[ORIGIN_ADMIN]))
    async with make_client(admin_permissions=["admin:apps:write"]) as client:
        resp = await client.post(
            f"{V1}/apps", json={"key": "other", "name": "Other", "origins": [ORIGIN_ADMIN]}
        )
    error = _error(resp, 409, "conflict")
    assert error["details"] == [{"field": "origins", "reason": "origin_taken"}]


async def test_app_patch_origin_taken_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok", origins=[ORIGIN_STOKTOK])
    await make_app(db_session, "other", origins=["https://other.mmlabs.online"])
    before = await _state(db_session)
    async with make_client(admin_permissions=["admin:apps:write"]) as client:
        resp = await client.patch(f"{V1}/apps/other", json={"origins": [ORIGIN_STOKTOK]})
        error = _error(resp, 409, "conflict")
        assert error["details"] == [{"field": "origins", "reason": "origin_taken"}]
        assert await _state(db_session) == before
        # An app keeps its own origins: re-sending them (plus a new one) is fine.
        resp = await client.patch(
            f"{V1}/apps/stoktok", json={"origins": [ORIGIN_STOKTOK, "https://s2.mmlabs.online"]}
        )
        assert resp.status_code == 200, resp.text


async def test_loopback_origins_exempt_from_uniqueness(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    local = "http://localhost:3000"
    await make_app(db_session, "stoktok", origins=[local])
    async with make_client(admin_permissions=["admin:apps:write"]) as client:
        resp = await client.post(
            f"{V1}/apps", json={"key": "other", "name": "Other", "origins": [local]}
        )
        assert resp.status_code == 201, resp.text
        resp = await client.patch(f"{V1}/apps/admin", json={"origins": [local]})
    _wildcard_required(resp)  # G1 still applies to the admin app


# --- user status (G5, USR-02c F2) -------------------------------------------------


@pytest.mark.parametrize(("start", "target"), [("active", "disabled"), ("disabled", "active")])
async def test_self_status_change_403(
    make_client: MakeClient, db_session: AsyncSession, start: str, target: str
) -> None:
    me = await _verified_user(db_session, status=start)
    before = await _state(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=me) as client:
        resp = await client.patch(f"{V1}/users/{me.id}", json={"status": target})
    assert _error(resp, 403, "self_status_change_forbidden")["details"] is None
    assert await _state(db_session) == before


async def test_disable_revokes_account_sessions(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_login_app(db_session)
    member = await make_member_role(db_session, app)
    user = await _verified_user(db_session)
    other = await _verified_user(db_session)
    await assign(db_session, user, member)
    raw, device_1, (mine_1, theirs) = await make_device(db_session, user, other)
    _, device_2, (mine_2,) = await make_device(db_session, user)

    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(f"{V1}/users/{user.id}", json={"status": "disabled"})
        assert resp.status_code == 200, resp.text
        token = await client.post(
            f"{V1}/auth/token",
            json={"audience": "stoktok", "account_id": str(mine_1.id)},
            headers=auth_headers(cookie=raw),
        )

    revoked = dict(
        (await db_session.execute(text("SELECT id, revoked_at FROM account_sessions"))).all()
    )
    assert revoked[mine_1.id] is not None
    assert revoked[mine_2.id] is not None
    assert revoked[theirs.id] is None
    devices = dict(
        (await db_session.execute(text("SELECT id, revoked_at FROM device_sessions"))).all()
    )
    assert devices[device_1.id] is None
    assert devices[device_2.id] is None
    assert token.status_code == 401, token.text


async def test_enable_does_not_touch_sessions(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await _verified_user(db_session, status="disabled")
    await make_device(db_session, user)
    before = (await _state(db_session))["account_sessions"]
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(f"{V1}/users/{user.id}", json={"status": "active"})
    assert resp.status_code == 200, resp.text
    assert (await _state(db_session))["account_sessions"] == before


async def test_disabled_user_pending_reset_token_unusable(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    user = await _verified_user(db_session)
    raw = secrets.token_urlsafe(32)
    await EmailTokenRepository(db_session).issue(user.id, "reset", sha256(raw), timedelta(hours=1))
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(f"{V1}/users/{user.id}", json={"status": "disabled"})
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"{V1}/auth/password/reset",
            json={"token": raw, "new_password": "a brand new passphrase 42"},
        )
    assert _error(resp, 422, "token_invalid")
