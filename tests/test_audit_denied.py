"""Denied privileged attempts (ADM-004b): one `outcome=denied` row per guard refusal of a staff
principal; the HTTP error and the business state are unchanged.

Setup is committed before each request: `record_failure` rolls back first, which under
`create_savepoint` would erase uncommitted setup. Values are copied before the request (the
rollback expires ORM objects).
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit_service
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditRepository
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Permission, Role
from app.modules.users.models import User
from tests.auth_factories import make_login_user
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
WILDCARD = {"required": "admin:*"}


@dataclass(frozen=True)
class Denied:
    method: str
    url: str
    body: dict[str, Any] | None
    perms: list[str]
    status: int
    code: str
    details: Any
    action: str
    target: tuple[str, uuid.UUID] | None
    app_key: str | None
    changes: dict[str, Any]
    org_id: uuid.UUID | None = None


Setup = Callable[[AsyncSession, User], Awaitable[Denied]]


async def _admin_app(db: AsyncSession) -> App:
    app = await db.scalar(select(App).where(App.key == "admin"))
    assert app is not None
    return app


async def _admin_perm(db: AsyncSession, key: str = "admin:users:read") -> Permission:
    perm = await db.scalar(select(Permission).where(Permission.key == key))
    assert perm is not None
    return perm


async def _admin_role(db: AsyncSession) -> Role:
    role = await make_role(db, await _admin_app(db), "admin:auditor")
    await grant(db, role, await _admin_perm(db))
    return role


def _wildcard(**kw: Any) -> dict[str, Any]:
    return {"status": 403, "code": "admin_wildcard_required", "details": WILDCARD, **kw}


async def _app_update(db: AsyncSession, actor: User) -> Denied:
    return Denied(
        "PATCH",
        f"{V1}/apps/admin",
        {"name": "Admin 2"},
        ["admin:apps:write"],
        **_wildcard(),
        action="app.updated",
        target=None,
        app_key="admin",
        changes={"reason": "admin_wildcard_required"},
    )


async def _permission_create(db: AsyncSession, actor: User) -> Denied:
    return Denied(
        "POST",
        f"{V1}/apps/admin/permissions",
        {"key": "admin:x:read"},
        ["admin:roles:manage"],
        **_wildcard(),
        action="permission.created",
        target=None,
        app_key="admin",
        changes={"reason": "admin_wildcard_required"},
    )


async def _permission_update(db: AsyncSession, actor: User) -> Denied:
    perm_id = (await _admin_perm(db)).id
    return Denied(
        "PATCH",
        f"{V1}/apps/admin/permissions/{perm_id}",
        {"description": "d"},
        ["admin:roles:manage"],
        **_wildcard(),
        action="permission.updated",
        target=("permission", perm_id),
        app_key="admin",
        changes={"reason": "admin_wildcard_required"},
    )


async def _permission_delete(db: AsyncSession, actor: User) -> Denied:
    perm_id = (await _admin_perm(db)).id
    return Denied(
        "DELETE",
        f"{V1}/apps/admin/permissions/{perm_id}",
        None,
        ["admin:roles:manage"],
        **_wildcard(),
        action="permission.deleted",
        target=("permission", perm_id),
        app_key="admin",
        changes={"reason": "admin_wildcard_required"},
    )


async def _role_create(db: AsyncSession, actor: User) -> Denied:
    return Denied(
        "POST",
        f"{V1}/apps/admin/roles",
        {"key": "admin:viewer", "name": "Viewer"},
        ["admin:roles:manage"],
        **_wildcard(),
        action="role.created",
        target=None,
        app_key="admin",
        changes={"reason": "admin_wildcard_required"},
    )


async def _role_update(db: AsyncSession, actor: User) -> Denied:
    role_id = (await _admin_role(db)).id
    return Denied(
        "PATCH",
        f"{V1}/apps/admin/roles/{role_id}",
        {"name": "Renamed"},
        ["admin:roles:manage"],
        **_wildcard(),
        action="role.updated",
        target=("role", role_id),
        app_key="admin",
        changes={"reason": "admin_wildcard_required", "role_key": "admin:auditor"},
    )


async def _role_delete(db: AsyncSession, actor: User) -> Denied:
    role_id = (await _admin_role(db)).id
    return Denied(
        "DELETE",
        f"{V1}/apps/admin/roles/{role_id}",
        None,
        ["admin:roles:manage"],
        **_wildcard(),
        action="role.deleted",
        target=("role", role_id),
        app_key="admin",
        changes={"reason": "admin_wildcard_required", "role_key": "admin:auditor"},
    )


async def _role_replace(db: AsyncSession, actor: User) -> Denied:
    role_id = (await _admin_role(db)).id
    body = {"permission_keys": ["admin:users:write"]}
    return Denied(
        "PUT",
        f"{V1}/apps/admin/roles/{role_id}/permissions",
        body,
        ["admin:roles:manage"],
        **_wildcard(),
        action="role.permissions_replaced",
        target=("role", role_id),
        app_key="admin",
        changes={
            "reason": "admin_wildcard_required",
            "role_key": "admin:auditor",
            "permission_keys_added": ["admin:users:write"],
            "permission_keys_removed": ["admin:users:read"],
        },
    )


async def _assign(db: AsyncSession, actor: User) -> Denied:
    role_id = (await _admin_role(db)).id
    user_id = (await make_login_user(db)).id
    return Denied(
        "POST",
        f"{V1}/users/{user_id}/role-assignments",
        {"role_id": str(role_id)},
        ["admin:roles:assign"],
        **_wildcard(),
        action="role_assignment.granted",
        target=("user", user_id),
        app_key="admin",
        changes={"reason": "admin_wildcard_required", "role_key": "admin:auditor"},
    )


async def _assign_self(db: AsyncSession, actor: User) -> Denied:
    role_id = (await _admin_role(db)).id
    return Denied(
        "POST",
        f"{V1}/users/{actor.id}/role-assignments",
        {"role_id": str(role_id)},
        SUPER,
        403,
        "self_assignment_forbidden",
        None,
        action="role_assignment.granted",
        target=("user", actor.id),
        app_key="admin",
        changes={"reason": "self_assignment_forbidden", "role_key": "admin:auditor"},
    )


async def _assign_org_scoped(db: AsyncSession, actor: User) -> Denied:
    """A privileged (`is_admin`) role of an org-scoped app: `org_id` is carried."""
    app = await make_app(db, "stoktok", org_mode="required")
    perm = await make_permission(db, app, "stoktok:*", is_admin=True)
    role = await make_role(db, app, "stoktok:admin")
    await grant(db, role, perm)
    org = Organization(slug=f"org-{uuid.uuid4().hex[:8]}", name="Org")
    db.add(org)
    await db.flush()
    org_id = org.id
    user_id = (await make_login_user(db)).id
    body = {"role_id": str(role.id), "org_id": str(org_id)}
    return Denied(
        "POST",
        f"{V1}/users/{user_id}/role-assignments",
        body,
        ["admin:roles:assign"],
        **_wildcard(),
        action="role_assignment.granted",
        target=("user", user_id),
        app_key="stoktok",
        org_id=org_id,
        changes={"reason": "admin_wildcard_required", "role_key": "stoktok:admin"},
    )


async def _batch_wildcard(db: AsyncSession, actor: User) -> Denied:
    """The bulk route's request-level refusal writes the single POST's row (one existing user)."""
    app = await make_app(db, "stoktok")
    perm = await make_permission(db, app, "stoktok:*", is_admin=True)
    role = await make_role(db, app, "stoktok:admin")
    await grant(db, role, perm)
    user_id = (await make_login_user(db)).id
    return Denied(
        "POST",
        f"{V1}/apps/stoktok/roles/{role.id}/assignment-batches",
        {"user_ids": [str(user_id)]},
        ["admin:roles:assign"],
        **_wildcard(),
        action="role_assignment.granted",
        target=("user", user_id),
        app_key="stoktok",
        changes={"reason": "admin_wildcard_required", "role_key": "stoktok:admin"},
    )


async def _revoke(db: AsyncSession, actor: User) -> Denied:
    user = await make_login_user(db)
    ra_id = (await assign(db, user, await _admin_role(db))).id
    return Denied(
        "DELETE",
        f"{V1}/users/{user.id}/role-assignments/{ra_id}",
        None,
        ["admin:roles:assign"],
        **_wildcard(),
        action="role_assignment.revoked",
        target=("role_assignment", ra_id),
        app_key="admin",
        changes={"reason": "admin_wildcard_required", "role_key": "admin:auditor"},
    )


async def _revoke_last_super_admin(db: AsyncSession, actor: User) -> Denied:
    only = await make_login_user(db)
    ra_id = (await assign(db, only, await super_admin_role(db))).id
    return Denied(
        "DELETE",
        f"{V1}/users/{only.id}/role-assignments/{ra_id}",
        None,
        SUPER,
        409,
        "last_super_admin",
        None,
        action="role_assignment.revoked",
        target=("role_assignment", ra_id),
        app_key="admin",
        changes={"reason": "last_super_admin", "role_key": "admin:super-admin"},
    )


async def _status_self(db: AsyncSession, actor: User) -> Denied:
    return Denied(
        "PATCH",
        f"{V1}/users/{actor.id}",
        {"status": "disabled"},
        SUPER,
        403,
        "self_status_change_forbidden",
        None,
        action="user.disabled",
        target=("user", actor.id),
        app_key=None,
        changes={"reason": "self_status_change_forbidden"},
    )


async def _status_privileged(db: AsyncSession, actor: User) -> Denied:
    staff = await make_login_user(db)
    await assign(db, staff, await _admin_role(db))
    return Denied(
        "PATCH",
        f"{V1}/users/{staff.id}",
        {"status": "disabled"},
        ["admin:users:write"],
        **_wildcard(),
        action="user.disabled",
        target=("user", staff.id),
        app_key=None,
        changes={"reason": "admin_wildcard_required"},
    )


async def _status_last_super_admin(db: AsyncSession, actor: User) -> Denied:
    only = await make_login_user(db)
    await assign(db, only, await super_admin_role(db))
    return Denied(
        "PATCH",
        f"{V1}/users/{only.id}",
        {"status": "disabled"},
        SUPER,
        409,
        "last_super_admin",
        None,
        action="user.disabled",
        target=("user", only.id),
        app_key=None,
        changes={"reason": "last_super_admin"},
    )


SITES: dict[str, Setup] = {
    "app_update": _app_update,
    "permission_create": _permission_create,
    "permission_update": _permission_update,
    "permission_delete": _permission_delete,
    "role_create": _role_create,
    "role_update": _role_update,
    "role_delete": _role_delete,
    "role_replace_permissions": _role_replace,
    "assign": _assign,
    "assign_self": _assign_self,
    "assign_org_scoped": _assign_org_scoped,
    "batch_wildcard": _batch_wildcard,
    "revoke": _revoke,
    "revoke_last_super_admin": _revoke_last_super_admin,
    "status_self": _status_self,
    "status_privileged": _status_privileged,
    "status_last_super_admin": _status_last_super_admin,
}


@pytest.fixture
def audit_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> Any:
    """Alembic's `fileConfig` (session fixture) disables loggers created before it ran."""
    monkeypatch.setattr(audit_service.logger, "disabled", False)
    caplog.set_level(logging.INFO)
    return caplog


async def _rows(db: AsyncSession) -> list[AuditLog]:
    return list(
        await db.scalars(
            select(AuditLog)
            .order_by(AuditLog.occurred_at, AuditLog.id)
            .execution_options(populate_existing=True)
        )
    )


async def _actor(db: AsyncSession) -> tuple[User, uuid.UUID]:
    actor = await make_login_user(db)  # a real, verified row (`granted_by`, counting holders)
    return actor, actor.id


def _assert_error(resp: Any, case: Denied) -> None:
    assert resp.status_code == case.status, resp.text
    error = resp.json()["error"]
    assert (error["code"], error["details"]) == (case.code, case.details)
    assert set(error) == {"code", "message", "details"}


@pytest.mark.parametrize("site", list(SITES))
async def test_denied_writes_one_row(
    make_strict_client: MakeClient, db_session: AsyncSession, site: str
) -> None:
    actor, actor_id = await _actor(db_session)
    case = await SITES[site](db_session, actor)
    await db_session.commit()
    before = len(await _rows(db_session))
    async with make_strict_client(admin_permissions=case.perms, admin_user=actor) as client:
        resp = await client.request(case.method, case.url, json=case.body)
    _assert_error(resp, case)
    rows = await _rows(db_session)
    assert len(rows) == before + 1
    row = rows[-1]
    assert (row.action, row.outcome) == (case.action, "denied")
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("user", actor_id, None)
    target = None if row.target_type is None else (row.target_type, row.target_id)
    assert target == case.target
    assert (row.app_key, row.org_id) == (case.app_key, case.org_id)
    assert row.changes == case.changes
    assert str(row.ip) == "127.0.0.1" and row.request_id


async def test_denied_leaves_state_unchanged(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    actor, _ = await _actor(db_session)
    only = await make_login_user(db_session)
    ra_id = (await assign(db_session, only, await super_admin_role(db_session))).id
    only_id = only.id
    await db_session.commit()
    async with make_strict_client(admin_permissions=["admin:apps:write"], admin_user=actor) as c:
        resp = await c.patch(f"{V1}/apps/admin", json={"name": "Admin 2"})
    assert resp.status_code == 403
    await db_session.refresh(actor)
    async with make_strict_client(admin_permissions=SUPER, admin_user=actor) as c:
        resp = await c.delete(f"{V1}/users/{only_id}/role-assignments/{ra_id}")
    assert resp.status_code == 409
    name = await db_session.scalar(text("SELECT name FROM apps WHERE key = 'admin'"))
    assert name != "Admin 2"
    still = await db_session.scalar(
        text("SELECT count(*) FROM role_assignments WHERE id = :id"), {"id": ra_id}
    )
    assert still == 1


async def test_plain_permission_403_writes_no_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    actor, _ = await _actor(db_session)
    await db_session.commit()
    before = len(await _rows(db_session))
    async with make_strict_client(admin_permissions=["admin:users:read"], admin_user=actor) as c:
        resp = await c.patch(f"{V1}/apps/admin", json={"name": "Admin 2"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"
    assert len(await _rows(db_session)) == before


async def test_non_guard_409_writes_no_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    actor, _ = await _actor(db_session)
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:write")
    role = await make_role(db_session, app, "stoktok:editor")
    await grant(db_session, role, perm)
    await assign(db_session, await make_login_user(db_session), role)
    perm_id = perm.id
    await db_session.commit()
    before = len(await _rows(db_session))
    async with make_strict_client(admin_permissions=SUPER, admin_user=actor) as c:
        resp = await c.patch(f"{V1}/apps/stoktok/permissions/{perm_id}", json={"is_admin": True})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "role_has_assignments"
    assert len(await _rows(db_session)) == before


async def test_audit_add_fails_denied_still_403(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    audit_logs: pytest.LogCaptureFixture,
) -> None:
    actor, _ = await _actor(db_session)
    case = await _app_update(db_session, actor)
    await db_session.commit()
    before = len(await _rows(db_session))

    async def boom(self: AuditRepository, row: AuditLog) -> None:
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(AuditRepository, "add", boom)
    async with make_strict_client(admin_permissions=case.perms, admin_user=actor) as client:
        resp = await client.request(case.method, case.url, json=case.body)
    _assert_error(resp, case)
    assert resp.json()["error"]["message"] == "Requires admin:*"
    assert len(await _rows(db_session)) == before
    assert "audit_write_failed action=app.updated outcome=denied" in audit_logs.text
    assert "@" not in audit_logs.text


async def test_audit_commit_fails_denied_still_409(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    audit_logs: pytest.LogCaptureFixture,
) -> None:
    actor, _ = await _actor(db_session)
    case = await _revoke_last_super_admin(db_session, actor)
    await db_session.commit()
    before = len(await _rows(db_session))

    async def boom(self: AuditRepository) -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AuditRepository, "commit", boom)
    async with make_strict_client(admin_permissions=case.perms, admin_user=actor) as client:
        resp = await client.request(case.method, case.url, json=case.body)
    _assert_error(resp, case)
    assert resp.json()["error"]["message"] == "Cannot revoke the last active super-admin"
    assert len(await _rows(db_session)) == before
    assert "audit_write_failed action=role_assignment.revoked outcome=denied" in audit_logs.text


async def test_denied_row_with_nul_user_agent(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    actor, _ = await _actor(db_session)
    case = await _app_update(db_session, actor)
    await db_session.commit()
    headers = {"User-Agent": "evil\x00agent\x1f/1.0\x7f"}
    async with make_strict_client(admin_permissions=case.perms, admin_user=actor) as client:
        resp = await client.request(case.method, case.url, json=case.body, headers=headers)
    _assert_error(resp, case)
    row = (await _rows(db_session))[-1]
    assert (row.action, row.outcome, row.user_agent) == ("app.updated", "denied", "evilagent/1.0")
