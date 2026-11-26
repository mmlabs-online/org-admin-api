"""USR-38: the designated per-app super-admin role (ADR-0030).

A denied request rolls the shared test transaction back, so the denied call comes last.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AdminWildcardRequiredError
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.audit.models import AuditLog
from app.modules.email.dispatcher import EmailDispatcher
from app.modules.rbac.actor import SYSTEM_JOIN
from app.modules.rbac.authz import AuthzService, is_designated_super_admin_role
from app.modules.rbac.models import App, Permission, Role, RoleAssignment
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import AssignmentService, role_is_privileged
from app.modules.sessions.staff_service import StaffSessionService
from app.modules.users.models import User
from tests.auth_factories import make_login_user
from tests.conftest import MakeClient
from tests.fakes import FakeEmailSender
from tests.invitation_factories import (
    ACCEPT_URL,
    CREATE_URL,
    invitations,
    link_token,
    person,
    setup_calechip,
)
from tests.rbac_factories import (
    assign,
    grant,
    make_app,
    make_org,
    make_permission,
    make_role,
    make_user,
    super_admin_role,
)

APPS = "/api/v1/apps"
STAFF_WRITE = ["admin:apps:write", "admin:roles:assign", "admin:roles:manage"]


@dataclass
class World:
    app: App
    role: Role  # simap:super-admin, not yet designated
    perm: Permission
    org_perm: Permission
    actor: User
    staff: User  # holds an active app-scope admin:super-admin


async def _staff(db_session: AsyncSession, **kwargs: Any) -> User:
    user = await make_login_user(db_session, **kwargs)
    await assign(db_session, user, await super_admin_role(db_session))
    return user


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    app = await make_app(db_session, "simap", org_mode="required")
    perm = await make_permission(db_session, app, "simap:items:read")
    org_perm = await make_permission(db_session, app, "simap:orders:write", scope="org")
    role = await make_role(db_session, app, "simap:super-admin")
    await grant(db_session, role, perm)
    return World(
        app=app,
        role=role,
        perm=perm,
        org_perm=org_perm,
        actor=await make_user(db_session),
        staff=await _staff(db_session),
    )


@pytest.fixture
async def client(make_client: MakeClient, world: World) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"], admin_user=world.actor) as c:
        yield c


async def _designate(db_session: AsyncSession, w: World) -> None:
    w.app.super_admin_role_id = w.role.id
    await db_session.flush()


def _assert(resp: Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    error: dict[str, Any] = resp.json()["error"]
    assert error["code"] == code
    return error


def _reason(resp: Response, status: int, reason: str) -> None:
    error = _assert(resp, status, "unprocessable" if status == 422 else "conflict")
    assert error["details"][0]["reason"] == reason


async def _patch(c: AsyncClient, body: dict[str, Any], key: str = "simap") -> Response:
    return await c.patch(f"{APPS}/{key}", json=body)


def _grant_url(user_id: uuid.UUID) -> str:
    return f"/api/v1/users/{user_id}/role-assignments"


async def _grant(c: AsyncClient, user_id: uuid.UUID, role_id: uuid.UUID, **extra: Any) -> Response:
    return await c.post(_grant_url(user_id), json={"role_id": str(role_id), **extra})


async def _audit(db_session: AsyncSession, action: str, app_key: str = "simap") -> list[AuditLog]:
    rows = await db_session.scalars(
        select(AuditLog).where(AuditLog.action == action, AuditLog.app_key == app_key)
    )
    return list(rows)


# --- pure ------------------------------------------------------------------------------------


def test_is_designated_super_admin_role_unit() -> None:
    role_id = uuid.uuid4()
    assert not is_designated_super_admin_role(App(key="a", super_admin_role_id=None), role_id)
    assert not is_designated_super_admin_role(
        App(key="a", super_admin_role_id=uuid.uuid4()), role_id
    )
    assert is_designated_super_admin_role(App(key="a", super_admin_role_id=role_id), role_id)


# --- 1: designation --------------------------------------------------------------------------


async def test_set_designation_happy(client: AsyncClient, world: World) -> None:
    resp = await _patch(client, {"super_admin_role_id": str(world.role.id)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["super_admin_role_id"] == str(world.role.id)
    got = await client.get(f"{APPS}/simap")
    assert got.json()["super_admin_role_id"] == str(world.role.id)


async def test_clear_designation_happy(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    resp = await _patch(client, {"super_admin_role_id": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["super_admin_role_id"] is None


async def test_set_designation_with_org_mode_in_same_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "later")
    role = await make_role(db_session, app, "later:super-admin")
    resp = await _patch(
        client, {"org_mode": "required", "super_admin_role_id": str(role.id)}, "later"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["super_admin_role_id"] == str(role.id)


async def test_set_designation_admin_app_422(client: AsyncClient, db_session: AsyncSession) -> None:
    role = await super_admin_role(db_session)
    resp = await _patch(client, {"super_admin_role_id": str(role.id)}, "admin")
    _reason(resp, 422, "admin_app_not_allowed")


async def test_set_designation_org_mode_none_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "plain")
    role = await make_role(db_session, app, "plain:super-admin")
    _reason(
        await _patch(client, {"super_admin_role_id": str(role.id)}, "plain"),
        422,
        "org_mode_required",
    )


async def test_set_designation_other_app_role_422(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    other = await make_app(db_session, "other", org_mode="required")
    role = await make_role(db_session, other, "other:super-admin")
    _reason(await _patch(client, {"super_admin_role_id": str(role.id)}), 422, "unknown_role")


async def test_set_designation_unknown_role_422(client: AsyncClient, world: World) -> None:
    _reason(await _patch(client, {"super_admin_role_id": str(uuid.uuid4())}), 422, "unknown_role")


async def test_set_designation_unknown_app_404(client: AsyncClient, world: World) -> None:
    resp = await _patch(client, {"super_admin_role_id": str(world.role.id)}, "nope")
    _assert(resp, 404, "not_found")


async def test_set_designation_no_token_401(make_client: MakeClient, world: World) -> None:
    async with make_client() as c:
        resp = await _patch(c, {"super_admin_role_id": str(world.role.id)})
    assert resp.status_code == 401


async def test_set_designation_without_apps_write_403(
    make_client: MakeClient, world: World
) -> None:
    async with make_client(admin_permissions=["admin:apps:read"], admin_user=world.actor) as c:
        resp = await _patch(c, {"super_admin_role_id": str(world.role.id)})
    _assert(resp, 403, "permission_denied")


async def test_set_designation_without_wildcard_403(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    async with make_client(admin_permissions=STAFF_WRITE, admin_user=world.actor) as c:
        resp = await _patch(c, {"super_admin_role_id": str(world.role.id)})
    _assert(resp, 403, "admin_wildcard_required")
    denied = await _audit(db_session, "app.updated")
    assert [r.outcome for r in denied] == ["denied"]


async def test_clear_designation_without_wildcard_403(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    async with make_client(admin_permissions=STAFF_WRITE, admin_user=world.actor) as c:
        resp = await _patch(c, {"super_admin_role_id": None})
    _assert(resp, 403, "admin_wildcard_required")


async def test_set_designation_system_role_422(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    member = await make_role(db_session, world.app, "simap:member")
    member.is_system = True
    await db_session.flush()
    _reason(await _patch(client, {"super_admin_role_id": str(member.id)}), 422, "system_role")


async def test_set_designation_default_role_422(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    world.app.default_role_id = world.role.id
    await db_session.flush()
    resp = await _patch(client, {"super_admin_role_id": str(world.role.id)})
    _reason(resp, 422, "same_as_default_role")


async def test_set_designation_org_owner_role_422(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    world.app.org_owner_role_id = world.role.id
    await db_session.flush()
    resp = await _patch(client, {"super_admin_role_id": str(world.role.id)})
    _reason(resp, 422, "same_as_org_owner_role")


async def test_set_default_role_to_designated_422(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    resp = await _patch(client, {"default_role_id": str(world.role.id)})
    _reason(resp, 422, "same_as_super_admin_role")


async def test_set_org_owner_role_to_designated_422(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    resp = await _patch(client, {"org_owner_role_id": str(world.role.id)})
    _reason(resp, 422, "same_as_super_admin_role")


async def test_set_designation_role_with_org_permission_422(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await grant(db_session, world.role, world.org_perm)
    resp = await _patch(client, {"super_admin_role_id": str(world.role.id)})
    _reason(resp, 422, "role_has_org_permissions")


async def test_set_designation_role_with_wildcard_422(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    wildcard = await make_permission(db_session, world.app, "simap:*", is_admin=True)
    await grant(db_session, world.role, wildcard)
    resp = await _patch(client, {"super_admin_role_id": str(world.role.id)})
    _reason(resp, 422, "wildcard_not_allowed")


@pytest.mark.parametrize("kind", ["app", "org", "expired"])
async def test_set_designation_role_with_assignments_409(
    client: AsyncClient, world: World, db_session: AsyncSession, kind: str
) -> None:
    holder = await make_user(db_session)
    if kind == "org":
        await assign(db_session, holder, world.role, org_id=(await make_org(db_session)).id)
    else:
        expires = datetime.now(UTC) - timedelta(days=1) if kind == "expired" else None
        await assign(db_session, holder, world.role, expires_at=expires)
    resp = await _patch(client, {"super_admin_role_id": str(world.role.id)})
    _reason(resp, 409, "role_has_assignments")


async def test_designation_change_is_audited(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _patch(client, {"super_admin_role_id": str(world.role.id)})
    await _patch(client, {"super_admin_role_id": None})
    rows = await _audit(db_session, "app.updated")
    found = [(r.changes or {}).get("super_admin_role_id") for r in rows]
    assert {"from": None, "to": str(world.role.id)} in found
    assert {"from": str(world.role.id), "to": None} in found


async def test_noop_designation_needs_no_wildcard(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    async with make_client(admin_permissions=STAFF_WRITE, admin_user=world.actor) as c:
        resp = await _patch(c, {"super_admin_role_id": str(world.role.id)})
    assert resp.status_code == 200, resp.text
    assert await _audit(db_session, "app.updated") == []


async def test_change_designation_to_other_role(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    other = await make_role(db_session, world.app, "simap:root")
    resp = await _patch(client, {"super_admin_role_id": str(other.id)})
    assert resp.status_code == 200, resp.text
    # The new role must still pass every rule.
    await grant(db_session, other, world.org_perm)
    third = await make_role(db_session, world.app, "simap:third")
    await grant(db_session, third, world.org_perm)
    _reason(
        await _patch(client, {"super_admin_role_id": str(third.id)}),
        422,
        "role_has_org_permissions",
    )


# --- 2: grant rules --------------------------------------------------------------------------


async def test_grant_to_staff_super_admin_201(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    resp = await _grant(client, world.staff.id, world.role.id)
    assert resp.status_code == 201, resp.text
    rows = await _audit(db_session, "role_assignment.granted")
    assert len(rows) == 1
    assert "simap:super-admin" in str(rows[0].changes)


async def test_grant_with_expiry_201(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    exp = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    resp = await _grant(client, world.staff.id, world.role.id, expires_at=exp)
    assert resp.status_code == 201, resp.text


async def _recipient(db_session: AsyncSession, case: str) -> User:
    if case == "no_admin_role":
        return await make_login_user(db_session)
    if case == "lesser_admin_role":
        admin = await db_session.scalar(select(App).where(App.key == "admin"))
        assert admin is not None
        role = await make_role(db_session, admin, "admin:lesser")
        user = await make_login_user(db_session)
        await assign(db_session, user, role)
        return user
    if case == "expired":
        user = await make_login_user(db_session)
        await assign(
            db_session,
            user,
            await super_admin_role(db_session),
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        return user
    if case == "disabled":
        return await _staff(db_session, status="disabled")
    return await _staff(db_session, verified=False)


@pytest.mark.parametrize(
    "case", ["no_admin_role", "lesser_admin_role", "expired", "disabled", "unverified"]
)
async def test_grant_recipient_not_staff_422(
    client: AsyncClient, world: World, db_session: AsyncSession, case: str
) -> None:
    await _designate(db_session, world)
    user = await _recipient(db_session, case)
    error = _assert(await _grant(client, user.id, world.role.id), 422, "unprocessable")
    assert error["details"] == [{"field": "user_id", "reason": "recipient_not_staff_super_admin"}]
    held = await db_session.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == user.id, RoleAssignment.role_id == world.role.id
        )
    )
    assert held is None


async def test_renew_expired_designated_assignment_rechecks_recipient(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    user = await make_login_user(db_session)
    await assign(db_session, user, world.role, expires_at=datetime.now(UTC) - timedelta(days=1))
    resp = await _grant(client, user.id, world.role.id)
    _reason(resp, 422, "recipient_not_staff_super_admin")


async def test_grant_with_org_id_422(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    org = await make_org(db_session)
    for org_id in (org.id, uuid.uuid4()):  # an unknown org id answers the same
        resp = await _grant(client, world.staff.id, world.role.id, org_id=str(org_id))
        error = _assert(resp, 422, "unprocessable")
        assert error["details"] == [{"field": "org_id", "reason": "org_id_not_allowed"}]


async def test_self_grant_403(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    async with make_client(admin_permissions=["admin:*"], admin_user=world.staff) as c:
        resp = await _grant(c, world.staff.id, world.role.id)
    _assert(resp, 403, "self_assignment_forbidden")
    denied = await _audit(db_session, "role_assignment.granted")
    assert [r.outcome for r in denied] == ["denied"]


async def test_grant_without_wildcard_403(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    plain = await make_login_user(db_session)  # not staff: still 403, no staff-status oracle
    async with make_client(admin_permissions=STAFF_WRITE, admin_user=world.actor) as c:
        resp = await _grant(c, plain.id, world.role.id)
    _assert(resp, 403, "admin_wildcard_required")


# --- 2d: revoke ------------------------------------------------------------------------------


async def test_revoke_designated_happy_204(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    ra = await assign(db_session, world.staff, world.role)
    resp = await client.delete(f"{_grant_url(world.staff.id)}/{ra.id}")
    assert resp.status_code == 204, resp.text


async def test_revoke_designated_without_wildcard_403(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    ra = await assign(db_session, world.staff, world.role)
    async with make_client(admin_permissions=STAFF_WRITE, admin_user=world.actor) as c:
        resp = await c.delete(f"{_grant_url(world.staff.id)}/{ra.id}")
    _assert(resp, 403, "admin_wildcard_required")


async def test_self_revoke_designated_403(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    ra = await assign(db_session, world.staff, world.role)
    async with make_client(admin_permissions=["admin:*"], admin_user=world.staff) as c:
        resp = await c.delete(f"{_grant_url(world.staff.id)}/{ra.id}")
    _assert(resp, 403, "self_assignment_forbidden")


# --- 2e: other grant paths -------------------------------------------------------------------


async def test_system_actor_grant_refused(world: World, db_session: AsyncSession) -> None:
    await _designate(db_session, world)
    with pytest.raises(AdminWildcardRequiredError):
        await AssignmentService(db_session)._grant_locked(
            world.app, world.role, world.staff.id, None, None, actor=SYSTEM_JOIN
        )


async def test_invitation_create_designated_role_403(
    make_client: MakeClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    role = await make_role(db_session, env.app, "calechip:super-admin")
    await grant(db_session, role, env.perms["data:read"])  # a subset of the inviter's powers
    env.app.super_admin_role_id = role.id
    await db_session.flush()
    async with make_client() as c:
        resp = await c.post(
            CREATE_URL.format(org_id=env.org.id),
            json={
                "account_id": str(env.inviter.account.id),
                "email": "x@example.com",
                "role": "calechip:super-admin",
            },
            headers=env.inviter.headers(),
        )
    _assert(resp, 403, "invite_role_forbidden")
    await email_dispatcher.join()
    assert fake_email.sent == []


async def test_invitation_accept_after_designation_token_invalid(
    make_client: MakeClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    role = await make_role(db_session, env.app, "calechip:super-admin")
    await grant(db_session, role, env.perms["data:read"])
    async with make_client() as c:
        resp = await c.post(
            CREATE_URL.format(org_id=env.org.id),
            json={
                "account_id": str(env.inviter.account.id),
                "email": "invitee@example.com",
                "role": "calechip:super-admin",
            },
            headers=env.inviter.headers(),
        )
        assert resp.status_code == 201, resp.text
        await email_dispatcher.join()
        token = link_token(fake_email.sent[-1])
        env.app.super_admin_role_id = role.id
        await db_session.flush()
        invitee = await person(db_session, None, email="invitee@example.com")
        resp = await c.post(
            ACCEPT_URL,
            json={"account_id": str(invitee.account.id), "token": token},
            headers=invitee.headers(),
        )
    assert resp.status_code in (400, 401, 404, 410, 422), resp.text
    (row,) = await invitations(db_session)
    await db_session.refresh(row)
    assert row.accepted_at is None


# --- 3/4: delete, org_mode, role permissions -------------------------------------------------


async def test_delete_designated_role_409(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    resp = await client.delete(f"{APPS}/simap/roles/{world.role.id}")
    _reason(resp, 409, "super_admin_role_designated")


async def test_org_mode_none_while_designated_409(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    _reason(await _patch(client, {"org_mode": "none"}), 409, "super_admin_role_designated")


async def test_org_mode_none_after_clear_ok(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    assert (await _patch(client, {"super_admin_role_id": None})).status_code == 200
    resp = await _patch(client, {"org_mode": "none"})
    assert resp.status_code == 200, resp.text


def _put(c: AsyncClient, w: World, keys: list[str]) -> Any:
    return c.put(f"{APPS}/simap/roles/{w.role.id}/permissions", json={"permission_keys": keys})


async def test_replace_permissions_designated_org_permission_409(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    resp = await _put(client, world, ["simap:items:read", "simap:orders:write"])
    _reason(resp, 409, "role_has_org_permissions")


async def test_replace_permissions_designated_wildcard_409(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    await make_permission(db_session, world.app, "simap:*", is_admin=True)
    _reason(await _put(client, world, ["simap:*"]), 409, "wildcard_not_allowed")


async def test_replace_permissions_designated_unchanged_reput_200(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    async with make_client(admin_permissions=STAFF_WRITE, admin_user=world.actor) as c:
        resp = await _put(c, world, ["simap:items:read"])
    assert resp.status_code == 200, resp.text


async def test_replace_permissions_designated_needs_wildcard_403(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    await make_permission(db_session, world.app, "simap:other:read")
    async with make_client(admin_permissions=STAFF_WRITE, admin_user=world.actor) as c:
        resp = await _put(c, world, ["simap:items:read", "simap:other:read"])
    _assert(resp, 403, "admin_wildcard_required")
    denied = await _audit(db_session, "role.permissions_replaced")
    assert [r.outcome for r in denied] == ["denied"]


async def test_permission_scope_flip_to_org_held_by_designated_409(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    resp = await client.patch(f"{APPS}/simap/permissions/{world.perm.id}", json={"scope": "org"})
    _reason(resp, 409, "super_admin_role_holds_permission")


# --- 6: one predicate, G5, shared helper -----------------------------------------------------


async def test_privilege_predicates_agree(world: World, db_session: AsyncSession) -> None:
    lesser = AdminPrincipal(world.actor.id, frozenset(STAFF_WRITE), "test")
    admin_perm = await make_permission(db_session, world.app, "simap:danger:run", is_admin=True)
    admin_app = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin_app is not None
    roles = {
        "admin_app": (admin_app, await super_admin_role(db_session), True),
        "is_admin": (world.app, await make_role(db_session, world.app, "simap:dangerous"), True),
        "designated": (world.app, world.role, True),
        "plain": (world.app, await make_role(db_session, world.app, "simap:plain"), False),
    }
    await grant(db_session, roles["is_admin"][1], admin_perm)
    await _designate(db_session, world)
    repo = RbacRepository(db_session)
    for name, (app, role, expected) in roles.items():
        holds = await repo.role_has_admin_permission(role.id)
        assert role_is_privileged(app, role.id, holds) is expected, name
        if name == "admin_app":
            continue
        holder = await make_user(db_session)
        await assign(db_session, holder, role)
        assert await repo.user_has_privileged_assignment(holder.id) is expected, name
        guarded = False
        try:
            await AssignmentService(db_session)._guard_privileged(
                app, role.id, (await make_user(db_session)).id, lesser
            )
        except AdminWildcardRequiredError:
            guarded = True
        assert guarded is expected, name
    # Clearing the designation makes the former role ordinary.
    world.app.super_admin_role_id = None
    await db_session.flush()
    assert role_is_privileged(world.app, world.role.id, False) is False


async def test_role_definitions_is_super_admin_flag(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    resp = await client.get(f"{APPS}/simap/role-definitions")
    flags = {i["key"]: (i["is_super_admin"], i["privileged"]) for i in resp.json()["items"]}
    assert flags["simap:super-admin"] == (True, True)
    assert all(not v[0] for k, v in flags.items() if k != "simap:super-admin")


async def test_disable_holder_needs_wildcard_403(
    make_client: MakeClient, world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    holder = await make_user(db_session)
    await assign(db_session, holder, world.role)
    async with make_client(admin_permissions=["admin:users:write"], admin_user=world.actor) as c:
        resp = await c.patch(f"/api/v1/users/{holder.id}", json={"status": "disabled"})
    _assert(resp, 403, "admin_wildcard_required")


async def test_revoke_holder_sessions_needs_wildcard_403(
    world: World, db_session: AsyncSession
) -> None:
    """The staff session service uses the same G5 predicate (the routes need `admin:*` anyway)."""
    await _designate(db_session, world)
    holder = await make_user(db_session)
    await assign(db_session, holder, world.role)
    lesser = AdminPrincipal(world.actor.id, frozenset(STAFF_WRITE), "test")
    with pytest.raises(AdminWildcardRequiredError):
        await StaffSessionService(db_session, idle_days=30, max_days=365).list(
            holder.id, actor=lesser, limit=10, offset=0
        )


async def test_app_super_admin_access_true_for_live_holder(
    world: World, db_session: AsyncSession
) -> None:
    await _designate(db_session, world)
    await assign(db_session, world.staff, world.role)
    assert await AuthzService(db_session).app_super_admin_access(world.staff.id, world.app)


@pytest.mark.parametrize(
    "case",
    [
        "no_designation",
        "expired",
        "revoked",
        "cleared",
        "lost_staff",
        "disabled",
        "unverified",
        "app_disabled",
        "org_scope_only",
    ],
)
async def test_app_super_admin_access_false_when(
    world: World, db_session: AsyncSession, case: str
) -> None:
    await _designate(db_session, world)
    holder = world.staff
    ra = await assign(
        db_session,
        holder,
        world.role,
        expires_at=datetime.now(UTC) - timedelta(days=1) if case == "expired" else None,
        org_id=(await make_org(db_session)).id if case == "org_scope_only" else None,
    )
    if case == "no_designation" or case == "cleared":
        world.app.super_admin_role_id = None
    elif case == "revoked":
        await db_session.delete(ra)
    elif case == "lost_staff":
        staff_role = await super_admin_role(db_session)
        await db_session.execute(
            RoleAssignment.__table__.delete().where(  # type: ignore[attr-defined]
                RoleAssignment.user_id == holder.id, RoleAssignment.role_id == staff_role.id
            )
        )
    elif case == "disabled":
        holder.status = "disabled"
    elif case == "unverified":
        holder.email_verified_at = None
    elif case == "app_disabled":
        world.app.status = "disabled"
    await db_session.flush()
    assert not await AuthzService(db_session).app_super_admin_access(holder.id, world.app)


async def test_app_super_admin_access_false_for_admin_app(
    world: World, db_session: AsyncSession
) -> None:
    admin_app = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin_app is not None
    assert not await AuthzService(db_session).app_super_admin_access(world.staff.id, admin_app)
