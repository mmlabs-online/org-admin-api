"""POST /api/v1/auth/orgs/{org_id}/members/{user_id}/remove (USR-30, ADR-0026)."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.auth.throttle import EmailHasher
from app.modules.rbac.models import RoleAssignment
from app.modules.users.models import User
from tests.auth_factories import HMAC_KEY, ORIGIN, auth_headers, make_login_app
from tests.conftest import MakeClient
from tests.invitation_factories import Person, make_invitation, person
from tests.org_member_factories import (
    RemoveEnv,
    member,
    remove_body,
    remove_url,
    setup_removal,
)
from tests.rbac_factories import assign, grant, make_org, make_permission, make_role

ACTIONS = ("organization.member_removed", "organization.invitation_revoked")


def _error(resp: Any) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


async def _remove(
    client: AsyncClient,
    caller: Person,
    target: Person | uuid.UUID,
    org_id: uuid.UUID,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> Any:
    target_id = target if isinstance(target, uuid.UUID) else target.user.id
    return await client.post(
        remove_url(org_id, target_id),
        json=remove_body(caller) if body is None else body,
        headers=caller.headers() if headers is None else headers,
    )


async def _held(db: AsyncSession, who: Person) -> list[RoleAssignment]:
    return list(
        await db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == who.user.id))
    )


async def _rows(db: AsyncSession, org_id: uuid.UUID) -> list[AuditLog]:
    """This org's rows (the concurrency test commits rows; audit_log is append-only)."""
    return list(
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(ACTIONS), AuditLog.org_id == org_id)
            .order_by(AuditLog.occurred_at)
        )
    )


async def _super(db: AsyncSession, renv: RemoveEnv, **kw: object) -> Person:
    """A caller with a non-owner role holding every permission of the owner role."""
    perms = list(renv.env.perms.values())
    role = await make_role(db, renv.env.app, "calechip:org-deputy")
    await grant(db, role, renv.remove_perm, *[p for p in perms if p.scope == "org"])
    return await member(db, renv, role, **kw)


# --- success -------------------------------------------------------------------------------


async def test_owner_removes_member_204(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    resp = await _remove(client, renv.owner, target, renv.org.id)
    assert resp.status_code == 204 and resp.content == b""
    assert resp.headers["Cache-Control"] == "no-store"
    assert await _held(db_session, target) == []


async def test_only_this_app_and_org_touched(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    env = renv.env
    target = await member(db_session, renv, env.viewer)
    await assign(db_session, target.user, env.member)  # app scope
    other_org = await make_org(db_session)
    await assign(db_session, target.user, env.viewer, org_id=other_org.id)
    other_app = await make_login_app(db_session, "other", org_mode="required")
    other_role = await make_role(db_session, other_app, "other:org-viewer")
    await assign(db_session, target.user, other_role, org_id=renv.org.id)
    resp = await _remove(client, renv.owner, target, renv.org.id)
    assert resp.status_code == 204
    left = {(ra.role_id, ra.org_id) for ra in await _held(db_session, target)}
    assert left == {
        (env.member.id, None),
        (env.viewer.id, other_org.id),
        (other_role.id, renv.org.id),
    }
    assert await db_session.get(User, target.user.id) is not None


async def test_all_org_roles_of_target_removed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.owner)
    await assign(db_session, target.user, renv.env.viewer, org_id=renv.org.id)
    resp = await _remove(client, renv.owner, target, renv.org.id)
    assert resp.status_code == 204
    assert await _held(db_session, target) == []


async def test_owner_removes_co_owner_204(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    co_owner = await member(db_session, renv, renv.env.owner)
    assert (await _remove(client, renv.owner, co_owner, renv.org.id)).status_code == 204


async def test_delegate_removes_lower_member_204(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    manager = await member(db_session, renv, renv.manager)
    target = await member(db_session, renv, renv.env.viewer)
    assert (await _remove(client, manager, target, renv.org.id)).status_code == 204
    assert await _held(db_session, target) == []


# --- request gates -------------------------------------------------------------------------


async def test_no_cookie_401(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    headers = auth_headers(app_key="calechip")
    resp = await _remove(client, renv.owner, target, renv.org.id, headers=headers)
    assert resp.status_code == 401


async def test_bad_origin_403(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    headers = auth_headers("calechip", "https://evil.example", renv.owner.raw)
    resp = await _remove(client, renv.owner, target, renv.org.id, headers=headers)
    assert resp.status_code == 403
    assert len(await _held(db_session, target)) == 1


async def test_missing_app_header_403(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    headers = auth_headers(None, ORIGIN, renv.owner.raw)
    resp = await _remove(client, renv.owner, target, renv.org.id, headers=headers)
    assert resp.status_code == 403


async def test_disabled_app_403(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    renv.env.app.status = "disabled"
    await db_session.flush()
    resp = await _remove(client, renv.owner, target, renv.org.id)
    assert resp.status_code == 403
    assert len(await _held(db_session, target)) == 1


async def test_unverified_caller_403(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    caller = await member(db_session, renv, renv.manager, verified=False)
    resp = await _remove(client, caller, target, renv.org.id)
    assert resp.status_code == 403 and _error(resp)["code"] == "email_unverified"


async def test_account_id_is_caller_not_target(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    # The target's account is not signed in on the owner's device.
    resp = await _remove(
        client, renv.owner, target, renv.org.id, body={"account_id": str(target.account.id)}
    )
    assert resp.status_code == 401 and _error(resp)["code"] == "session_invalid"
    # The path names the caller: self-removal, whatever the body says.
    resp = await _remove(client, renv.owner, renv.owner, renv.org.id)
    assert resp.status_code == 403 and _error(resp)["code"] == "self_removal_forbidden"
    assert len(await _held(db_session, target)) == 1


async def test_org_access_denied_one_answer(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    suspended = await make_org(db_session, status="suspended")
    await assign(db_session, renv.owner.user, renv.env.owner, org_id=suspended.id)
    await assign(db_session, target.user, renv.env.viewer, org_id=suspended.id)
    foreign = await make_org(db_session)  # the owner holds no role there
    await assign(db_session, target.user, renv.env.viewer, org_id=foreign.id)
    bodies = []
    for org_id in (uuid.uuid4(), suspended.id, foreign.id):
        resp = await _remove(client, renv.owner, target, org_id)
        assert resp.status_code == 403 and _error(resp)["code"] == "org_access_denied"
        bodies.append(resp.json())
    assert bodies[0] == bodies[1] == bodies[2]
    assert len(await _held(db_session, target)) == 3


# --- permission and rank -------------------------------------------------------------------


async def test_member_without_permission_403(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.owner)
    caller = await member(db_session, renv, renv.env.viewer)
    resp = await _remove(client, caller, target, renv.org.id)
    assert resp.status_code == 403 and _error(resp)["code"] == "org_remove_member_forbidden"


async def test_permission_at_app_scope_or_other_org_does_not_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    at_app = await member(db_session, renv, renv.env.viewer)
    await assign(db_session, at_app.user, renv.manager)  # app scope
    in_other = await member(db_session, renv, renv.env.viewer)
    other_org = await make_org(db_session)
    await assign(db_session, in_other.user, renv.manager, org_id=other_org.id)
    for caller in (at_app, in_other):
        resp = await _remove(client, caller, target, renv.org.id)
        assert resp.status_code == 403 and _error(resp)["code"] == "org_remove_member_forbidden"
    assert len(await _held(db_session, target)) == 1


async def test_self_removal_403(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    manager = await member(db_session, renv, renv.manager)
    resp = await _remove(client, manager, manager, renv.org.id)
    assert resp.status_code == 403 and _error(resp)["code"] == "self_removal_forbidden"


async def test_sole_owner_self_removal_is_403_not_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    resp = await _remove(client, renv.owner, renv.owner, renv.org.id)
    assert resp.status_code == 403 and _error(resp)["code"] == "self_removal_forbidden"


async def test_delegate_cannot_remove_owner_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    manager = await member(db_session, renv, renv.manager)
    resp = await _remove(client, manager, renv.owner, renv.org.id)
    assert resp.status_code == 403 and _error(resp)["code"] == "member_outranks_caller"
    assert len(await _held(db_session, renv.owner)) == 2  # app member + org owner


async def test_target_with_admin_permission_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    admin_perm = await make_permission(
        db_session, renv.env.app, "calechip:team:manage", is_admin=True, scope="org"
    )
    admin_role = await make_role(db_session, renv.env.app, "calechip:org-admin")
    await grant(db_session, admin_role, admin_perm, renv.remove_perm)
    caller = await member(db_session, renv, admin_role)  # holds everything the target holds
    target = await member(db_session, renv, admin_role)
    resp = await _remove(client, caller, target, renv.org.id)
    assert resp.status_code == 403 and _error(resp)["code"] == "member_outranks_caller"


# --- last owner ----------------------------------------------------------------------------


async def test_last_owner_409(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    caller = await _super(db_session, renv)
    resp = await _remove(client, caller, renv.owner, renv.org.id)
    assert resp.status_code == 409 and _error(resp)["code"] == "last_org_owner"
    assert len(await _held(db_session, renv.owner)) == 2  # app member + org owner


@pytest.mark.parametrize("variant", ["expiring", "disabled", "unverified"])
async def test_expiring_or_inactive_owner_does_not_count(
    client: AsyncClient, db_session: AsyncSession, variant: str
) -> None:
    renv = await setup_removal(db_session)
    caller = await _super(db_session, renv)
    if variant == "expiring":
        other = await member(db_session, renv, renv.env.viewer)
        await assign(
            db_session,
            other.user,
            renv.env.owner,
            org_id=renv.org.id,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    else:
        kw: dict[str, Any] = (
            {"status": "disabled"} if variant == "disabled" else {"verified": False}
        )
        await member(db_session, renv, renv.env.owner, **kw)
    resp = await _remove(client, caller, renv.owner, renv.org.id)
    assert resp.status_code == 409 and _error(resp)["code"] == "last_org_owner"


# --- target not found ----------------------------------------------------------------------


async def test_unknown_target_404(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    resp = await _remove(client, renv.owner, uuid.uuid4(), renv.org.id)
    assert resp.status_code == 404 and _error(resp)["code"] == "member_not_found"


async def test_target_only_expired_roles_404(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await person(db_session, None)
    ra = await assign(db_session, target.user, renv.env.viewer, org_id=renv.org.id)
    await db_session.execute(
        update(RoleAssignment)
        .where(RoleAssignment.id == ra.id)
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    resp = await _remove(client, renv.owner, target, renv.org.id)
    assert resp.status_code == 404 and _error(resp)["code"] == "member_not_found"
    assert len(await _held(db_session, target)) == 1


async def test_target_roles_in_other_app_only_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    other_app = await make_login_app(db_session, "other", org_mode="required")
    other_role = await make_role(db_session, other_app, "other:org-viewer")
    target = await member(db_session, renv, other_role)
    resp = await _remove(client, renv.owner, target, renv.org.id)
    assert resp.status_code == 404 and _error(resp)["code"] == "member_not_found"
    assert len(await _held(db_session, target)) == 1


async def test_retry_after_success_404(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    assert (await _remove(client, renv.owner, target, renv.org.id)).status_code == 204
    resp = await _remove(client, renv.owner, target, renv.org.id)
    assert resp.status_code == 404 and _error(resp)["code"] == "member_not_found"


# --- app setup and validation --------------------------------------------------------------


async def test_org_mode_none_422(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    renv.env.app.org_owner_role_id = None
    renv.env.app.org_mode = "none"
    await db_session.flush()
    resp = await _remove(client, renv.owner, renv.owner, renv.org.id)
    assert resp.status_code == 422 and _error(resp)["code"] == "org_not_supported"


async def test_owner_role_unset_403(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    renv.env.app.org_owner_role_id = None
    await db_session.flush()
    resp = await _remove(client, renv.owner, target, renv.org.id)
    assert resp.status_code == 403 and _error(resp)["code"] == "org_member_remove_unavailable"
    assert len(await _held(db_session, target)) == 1


async def test_body_validation_422(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    extra = {**remove_body(renv.owner), "role": "calechip:org-viewer"}
    assert (await _remove(client, renv.owner, target, renv.org.id, body=extra)).status_code == 422
    bad = await client.post(
        f"/api/v1/auth/orgs/{renv.org.id}/members/not-a-uuid/remove",
        json=remove_body(renv.owner),
        headers=renv.owner.headers(),
    )
    assert bad.status_code == 422
    bad_body = {"account_id": "nope"}
    assert (
        await _remove(client, renv.owner, target, renv.org.id, body=bad_body)
    ).status_code == 422
    assert len(await _held(db_session, target)) == 1


# --- audit, invitations, tokens ------------------------------------------------------------


async def test_success_writes_one_audit_row_without_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.owner)
    await assign(db_session, target.user, renv.env.viewer, org_id=renv.org.id)
    assert (await _remove(client, renv.owner, target, renv.org.id)).status_code == 204
    (row,) = await _rows(db_session, renv.org.id)
    assert row.action == "organization.member_removed"
    assert row.actor_user_id == renv.owner.user.id
    assert (row.target_type, row.target_id) == ("user", target.user.id)
    assert (row.app_key, row.org_id) == ("calechip", renv.org.id)
    assert row.changes == {
        "role_keys": ["calechip:org-owner", "calechip:org-viewer"],
        "count": 2,
        "was_owner": True,
    }
    blob = f"{row.changes}"
    assert target.user.email not in blob and renv.owner.user.email not in blob


async def test_refusals_write_no_audit_row(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    manager = await member(db_session, renv, renv.manager)
    viewer = await member(db_session, renv, renv.env.viewer)
    for caller, target in [(manager, renv.owner), (viewer, manager), (manager, manager)]:
        assert (await _remove(client, caller, target, renv.org.id)).status_code == 403
    assert (await _remove(client, manager, uuid.uuid4(), renv.org.id)).status_code == 404
    caller = await _super(db_session, renv)
    assert (await _remove(client, caller, renv.owner, renv.org.id)).status_code == 409
    assert await _rows(db_session, renv.org.id) == []


async def test_pending_invitations_to_target_revoked(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    other_org = await make_org(db_session)
    now = datetime.now(UTC)
    hasher = EmailHasher(HMAC_KEY)
    pending, _ = await make_invitation(
        db_session, renv.org, renv.env.viewer, created_at=now, expires_at=now + timedelta(days=1)
    )
    elsewhere, _ = await make_invitation(
        db_session, other_org, renv.env.viewer, created_at=now, expires_at=now + timedelta(days=1)
    )
    for invitation in (pending, elsewhere):
        invitation.email_hmac = hasher.hmac(target.user.email.lower())
    await db_session.flush()
    assert (await _remove(client, renv.owner, target, renv.org.id)).status_code == 204
    await db_session.refresh(pending)
    await db_session.refresh(elsewhere)
    assert pending.revoked_at is not None and elsewhere.revoked_at is None
    revoked = [
        r
        for r in await _rows(db_session, renv.org.id)
        if r.action == "organization.invitation_revoked"
    ]
    assert len(revoked) == 1
    assert (revoked[0].target_id, revoked[0].changes) == (pending.id, {"reason": "member_removed"})
    assert [r.action for r in await _rows(db_session, renv.org.id)].count(
        "organization.member_removed"
    ) == 1


async def test_token_after_removal(client: AsyncClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    await assign(db_session, target.user, renv.env.member)  # app membership
    token = {"audience": "calechip", "account_id": str(target.account.id)}
    before = await client.post(
        "/api/v1/auth/token", json={**token, "org_id": str(renv.org.id)}, headers=target.headers()
    )
    assert before.status_code == 200
    assert (await _remove(client, renv.owner, target, renv.org.id)).status_code == 204
    after = await client.post(
        "/api/v1/auth/token", json={**token, "org_id": str(renv.org.id)}, headers=target.headers()
    )
    assert after.status_code == 403 and _error(after)["code"] == "org_access_denied"
    plain = await client.post("/api/v1/auth/token", json=token, headers=target.headers())
    assert plain.status_code == 200


async def test_denied_log_has_no_email(
    client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    renv = await setup_removal(db_session)
    manager = await member(db_session, renv, renv.manager)
    # Alembic's fileConfig (migrations in the session fixture) disables existing loggers.
    for name in ("app.modules.organizations.member_service", "app"):
        logging.getLogger(name).disabled = False
    with caplog.at_level(logging.INFO, logger="app"):
        denied = await _remove(client, manager, renv.owner, renv.org.id)
    assert denied.status_code == 403
    texts = [r.getMessage() for r in caplog.records if r.name.startswith("app.modules.")]
    assert any("org_member_remove_denied" in t for t in texts)  # an app-module line was captured
    assert all("@" not in t for t in caplog.messages)


# --- rate limits ---------------------------------------------------------------------------


async def test_rate_limit_per_ip_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    async with make_client(org_member_remove_rate_limit=1) as client:
        first = await _remove(client, renv.owner, target, renv.org.id)
        second = await _remove(client, renv.owner, target, renv.org.id)
    assert first.status_code == 204
    assert second.status_code == 429 and "Retry-After" in second.headers
    assert _error(second)["code"] == "rate_limited"


async def test_rate_limit_per_user_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    renv = await setup_removal(db_session)
    target = await member(db_session, renv, renv.env.viewer)
    async with make_client(org_member_remove_user_rate_limit=1) as client:
        first = await _remove(client, renv.owner, target, renv.org.id)
        second = await _remove(client, renv.owner, target, renv.org.id)
    assert first.status_code == 204
    assert second.status_code == 429 and "Retry-After" in second.headers
