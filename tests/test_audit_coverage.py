"""Every mutating Bearer route writes exactly one audit row on success, none on failure (ADM-004).

`test_every_mutating_bearer_route_is_mapped` fails for a new mutating route until it is added to
`AUDITED` with a success and a failure scenario; `test_every_mutating_identity_route_is_classified`
does the same for identity routes (ADM-004b): audited (with a success scenario) or not.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.auth.email_verification import VERIFY, VERIFY_TTL
from app.modules.auth.password_reset import RESET, RESET_TTL
from app.modules.auth.repository import EmailTokenRepository
from app.modules.bug_reports.models import BugReport
from app.modules.entitlements.models import OrgEntitlement
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Permission, Role
from app.modules.usage.models import UsageMetric
from app.modules.users.models import User
from tests.app_credential_factories import make_credential
from tests.auth_factories import (
    LOGIN,
    PASSWORD,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    make_member_role,
    sha256,
)
from tests.conftest import MakeClient
from tests.deployer_credential_factories import make_deployer_credential
from tests.invitation_factories import make_invitation
from tests.join_request_factories import make_approval_app, make_join_request
from tests.rbac_factories import (
    assign,
    grant,
    make_app,
    make_permission,
    make_role,
    make_user,
    super_admin_role,
)
from tests.release_factories import make_release
from tests.test_route_auth_classes import APP_CREDENTIAL, BEARER, COOKIE, PUBLIC

V1 = "/api/v1"
Call = tuple[str, Any]  # (url, json body or None)
Scenario = Callable[[AsyncSession], Awaitable[Call]]


def _key() -> str:
    return f"app-{uuid.uuid4().hex[:8]}"


async def _app(s: AsyncSession) -> App:
    return await make_app(s, _key())


async def _org(s: AsyncSession) -> Organization:
    org = Organization(slug=f"org-{uuid.uuid4().hex[:8]}", name="Org")
    s.add(org)
    await s.flush()
    return org


async def _perm(s: AsyncSession) -> tuple[App, Permission]:
    app = await _app(s)
    return app, await make_permission(s, app, f"{app.key}:items:read")


async def _role(s: AsyncSession) -> tuple[App, Role]:
    app = await _app(s)
    return app, await make_role(s, app, f"{app.key}:viewer")


# --- success scenarios -------------------------------------------------------


async def _org_create(s: AsyncSession) -> Call:
    return f"{V1}/organizations", {"slug": f"org-{uuid.uuid4().hex[:8]}", "name": "Org"}


async def _org_update(s: AsyncSession) -> Call:
    return f"{V1}/organizations/{(await _org(s)).id}", {"name": "Renamed"}


async def _org_delete(s: AsyncSession) -> Call:
    return f"{V1}/organizations/{(await _org(s)).id}", None


async def _user_status(s: AsyncSession) -> Call:
    # No pending requests, so exactly one row; with requests: see
    # `test_user_disable_with_pending_requests_writes_multiple_rows`.
    return f"{V1}/users/{(await make_user(s)).id}", {"status": "disabled"}


async def _session_revoke(s: AsyncSession) -> Call:
    user = await make_user(s)
    _, _, (account,) = await make_device(s, user)
    return f"{V1}/users/{user.id}/sessions/{account.id}", None


async def _sessions_revoke_all(s: AsyncSession) -> Call:
    user = await make_user(s)
    await make_device(s, user)
    return f"{V1}/users/{user.id}/sessions", None


async def _assign(s: AsyncSession) -> Call:
    _, role = await _role(s)
    return f"{V1}/users/{(await make_user(s)).id}/role-assignments", {"role_id": str(role.id)}


async def _revoke(s: AsyncSession) -> Call:
    _, role = await _role(s)
    user = await make_user(s)
    ra = await assign(s, user, role)
    return f"{V1}/users/{user.id}/role-assignments/{ra.id}", None


async def _app_create(s: AsyncSession) -> Call:
    return f"{V1}/apps", {"key": _key(), "name": "New app"}


async def _app_update(s: AsyncSession) -> Call:
    return f"{V1}/apps/{(await _app(s)).key}", {"name": "Renamed"}


async def _app_delete(s: AsyncSession) -> Call:
    return f"{V1}/apps/{(await _app(s)).key}", None


async def _perm_create(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/permissions", {"key": f"{app.key}:items:read"}


async def _perm_update(s: AsyncSession) -> Call:
    app, perm = await _perm(s)
    return f"{V1}/apps/{app.key}/permissions/{perm.id}", {"description": "Read items"}


async def _perm_delete(s: AsyncSession) -> Call:
    app, perm = await _perm(s)
    return f"{V1}/apps/{app.key}/permissions/{perm.id}", None


async def _inv_revoke(s: AsyncSession) -> Call:
    _, role = await _role(s)
    org = await _org(s)
    now = datetime.now(UTC)
    inv, _ = await make_invitation(
        s, org, role, created_at=now - timedelta(hours=1), expires_at=now + timedelta(days=1)
    )
    return f"{V1}/organizations/{org.id}/invitations/{inv.id}", None


async def _role_create(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/roles", {"key": f"{app.key}:viewer", "name": "Viewer"}


async def _role_update(s: AsyncSession) -> Call:
    app, role = await _role(s)
    return f"{V1}/apps/{app.key}/roles/{role.id}", {"name": "Renamed"}


async def _role_delete(s: AsyncSession) -> Call:
    app, role = await _role(s)
    return f"{V1}/apps/{app.key}/roles/{role.id}", None


async def _role_permissions(s: AsyncSession) -> Call:
    app, role = await _role(s)
    perm = await make_permission(s, app, f"{app.key}:items:read")
    return f"{V1}/apps/{app.key}/roles/{role.id}/permissions", {"permission_keys": [perm.key]}


async def _cred_issue(s: AsyncSession) -> Call:
    return f"{V1}/apps/{(await _app(s)).key}/credentials", None


async def _cred_revoke(s: AsyncSession) -> Call:
    app = await _app(s)
    cred, _ = await make_credential(s, app)
    return f"{V1}/apps/{app.key}/credentials/{cred.id}", None


async def _deployer_issue(s: AsyncSession) -> Call:
    return f"{V1}/deployer-credentials", None


async def _deployer_issue_at_limit(s: AsyncSession) -> Call:
    for _ in range(2):
        await make_deployer_credential(s)
    return f"{V1}/deployer-credentials", None


async def _deployer_revoke(s: AsyncSession) -> Call:
    row, _ = await make_deployer_credential(s)
    return f"{V1}/deployer-credentials/{row.id}", None


async def _deployer_revoke_unknown(s: AsyncSession) -> Call:
    return f"{V1}/deployer-credentials/{uuid.uuid4()}", None


async def _jr_pending(s: AsyncSession) -> tuple[str, uuid.UUID]:
    app = await make_approval_app(s)
    request = await make_join_request(s, app, await make_login_user(s))
    return app.key, request.id


async def _jr_approve(s: AsyncSession) -> Call:
    key, request_id = await _jr_pending(s)
    return f"{V1}/apps/{key}/join-requests/{request_id}/approve", None


async def _jr_reject(s: AsyncSession) -> Call:
    key, request_id = await _jr_pending(s)
    return f"{V1}/apps/{key}/join-requests/{request_id}/reject", None


async def _ent_row(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    org, app = await _org(s), await make_app(s, _key(), org_mode="required")
    ent = OrgEntitlement(org_id=org.id, app_id=app.id, plan="pro")
    s.add(ent)
    await s.flush()
    return org.id, ent.id


async def _ent_grant(s: AsyncSession) -> Call:
    org, app = await _org(s), await make_app(s, _key(), org_mode="required")
    return f"{V1}/organizations/{org.id}/entitlements", {"app_key": app.key, "plan": "pro"}


async def _ent_update(s: AsyncSession) -> Call:
    org_id, ent_id = await _ent_row(s)
    return f"{V1}/organizations/{org_id}/entitlements/{ent_id}", {"plan": "max"}


async def _ent_revoke(s: AsyncSession) -> Call:
    org_id, ent_id = await _ent_row(s)
    return f"{V1}/organizations/{org_id}/entitlements/{ent_id}", None


async def _bug_done(s: AsyncSession) -> Call:
    app, user = await make_app(s, _key()), await make_user(s)
    report = BugReport(app_id=app.id, user_id=user.id, description="broken")
    s.add(report)
    await s.flush()
    return f"{V1}/bug-reports/{report.id}", {"status": "done"}


async def _bug_unknown(s: AsyncSession) -> Call:
    return f"{V1}/bug-reports/{uuid.uuid4()}", {"status": "done"}


async def _usage_row(s: AsyncSession) -> tuple[str, uuid.UUID]:
    app = await make_app(s, _key())
    metric = UsageMetric(
        app_id=app.id, key="chat-turns", display_name="Chat", unit="count", default_limit=5
    )
    s.add(metric)
    await s.flush()
    return app.key, metric.id


async def _usage_create(s: AsyncSession) -> Call:
    app = await make_app(s, _key())
    body = {
        "key": "chat-turns",
        "display_name": "Chat",
        "unit": "count",
        "default_limit": 5,
        "reason": "initial",
    }
    return f"{V1}/apps/{app.key}/usage-metrics", body


async def _usage_create_dup(s: AsyncSession) -> Call:
    app_key, _ = await _usage_row(s)
    body = {
        "key": "chat-turns",
        "display_name": "Chat",
        "unit": "count",
        "default_limit": 5,
        "reason": "again",
    }
    return f"{V1}/apps/{app_key}/usage-metrics", body


async def _usage_update(s: AsyncSession) -> Call:
    app_key, metric_id = await _usage_row(s)
    return f"{V1}/apps/{app_key}/usage-metrics/{metric_id}", {"default_limit": 9, "reason": "raise"}


async def _usage_unknown(s: AsyncSession) -> Call:
    app = await make_app(s, _key())
    return f"{V1}/apps/{app.key}/usage-metrics/{uuid.uuid4()}", {"default_limit": 9, "reason": "x"}


async def _release_delete(s: AsyncSession) -> Call:
    release = await make_release(s, await make_app(s, _key()))
    return f"{V1}/releases/{release.id}", None


async def _release_unknown(s: AsyncSession) -> Call:
    return f"{V1}/releases/{uuid.uuid4()}", None


# --- failure scenarios (404 unknown id/key, 409 duplicate) -------------------


async def _org_create_dup(s: AsyncSession) -> Call:
    return f"{V1}/organizations", {"slug": (await _org(s)).slug, "name": "Org"}


async def _org_unknown(s: AsyncSession) -> Call:
    return f"{V1}/organizations/{uuid.uuid4()}", {"name": "Renamed"}


async def _org_unknown_delete(s: AsyncSession) -> Call:
    return f"{V1}/organizations/{uuid.uuid4()}", None


async def _user_unknown(s: AsyncSession) -> Call:
    return f"{V1}/users/{uuid.uuid4()}", {"status": "disabled"}


async def _session_unknown(s: AsyncSession) -> Call:
    return f"{V1}/users/{(await make_user(s)).id}/sessions/{uuid.uuid4()}", None


async def _sessions_unknown_user(s: AsyncSession) -> Call:
    return f"{V1}/users/{uuid.uuid4()}/sessions", None


async def _assign_unknown_user(s: AsyncSession) -> Call:
    _, role = await _role(s)
    return f"{V1}/users/{uuid.uuid4()}/role-assignments", {"role_id": str(role.id)}


async def _revoke_unknown(s: AsyncSession) -> Call:
    return f"{V1}/users/{(await make_user(s)).id}/role-assignments/{uuid.uuid4()}", None


async def _app_create_dup(s: AsyncSession) -> Call:
    return f"{V1}/apps", {"key": (await _app(s)).key, "name": "Dup"}


async def _app_unknown(s: AsyncSession) -> Call:
    return f"{V1}/apps/nope-{uuid.uuid4().hex[:6]}", {"name": "Renamed"}


async def _app_unknown_delete(s: AsyncSession) -> Call:
    return f"{V1}/apps/nope-{uuid.uuid4().hex[:6]}", None


async def _perm_create_dup(s: AsyncSession) -> Call:
    app, perm = await _perm(s)
    return f"{V1}/apps/{app.key}/permissions", {"key": perm.key}


async def _perm_unknown(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/permissions/{uuid.uuid4()}", {"description": "x"}


async def _perm_unknown_delete(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/permissions/{uuid.uuid4()}", None


async def _role_create_dup(s: AsyncSession) -> Call:
    app, role = await _role(s)
    return f"{V1}/apps/{app.key}/roles", {"key": role.key, "name": "Dup"}


async def _inv_revoke_unknown(s: AsyncSession) -> Call:
    return f"{V1}/organizations/{(await _org(s)).id}/invitations/{uuid.uuid4()}", None


async def _role_unknown(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/roles/{uuid.uuid4()}", {"name": "Renamed"}


async def _role_unknown_delete(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/roles/{uuid.uuid4()}", None


async def _role_unknown_permissions(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/roles/{uuid.uuid4()}/permissions", {"permission_keys": []}


async def _cred_issue_unknown(s: AsyncSession) -> Call:
    return f"{V1}/apps/nope-{uuid.uuid4().hex[:6]}/credentials", None


async def _cred_revoke_unknown(s: AsyncSession) -> Call:
    return f"{V1}/apps/{(await _app(s)).key}/credentials/{uuid.uuid4()}", None


async def _jr_unknown_approve(s: AsyncSession) -> Call:
    app = await make_approval_app(s)
    return f"{V1}/apps/{app.key}/join-requests/{uuid.uuid4()}/approve", None


async def _jr_unknown_reject(s: AsyncSession) -> Call:
    app = await make_approval_app(s)
    return f"{V1}/apps/{app.key}/join-requests/{uuid.uuid4()}/reject", None


async def _ent_grant_unknown_org(s: AsyncSession) -> Call:
    app = await make_app(s, _key(), org_mode="required")
    return f"{V1}/organizations/{uuid.uuid4()}/entitlements", {"app_key": app.key, "plan": "pro"}


async def _ent_unknown_update(s: AsyncSession) -> Call:
    org = await _org(s)
    return f"{V1}/organizations/{org.id}/entitlements/{uuid.uuid4()}", {"plan": "max"}


async def _ent_unknown_revoke(s: AsyncSession) -> Call:
    org = await _org(s)
    return f"{V1}/organizations/{org.id}/entitlements/{uuid.uuid4()}", None


ORG, ORG_ID = f"{V1}/organizations", f"{V1}/organizations/{{org_id}}"
USER, ASSIGN = f"{V1}/users/{{user_id}}", f"{V1}/users/{{user_id}}/role-assignments"
APP, PERMS, ROLES = (
    f"{V1}/apps/{{key}}",
    f"{V1}/apps/{{key}}/permissions",
    f"{V1}/apps/{{key}}/roles",
)
CREDS = f"{V1}/apps/{{key}}/credentials"
DEPLOYER = f"{V1}/deployer-credentials"
JREQ = f"{V1}/apps/{{key}}/join-requests"
ENT = f"{V1}/organizations/{{org_id}}/entitlements"

# route -> (allowed actions, success scenario, failure scenario, failure status)
AUDITED: dict[tuple[str, str], tuple[set[str], Scenario, Scenario, int]] = {
    ("POST", ORG): ({"organization.created"}, _org_create, _org_create_dup, 409),
    ("PATCH", ORG_ID): ({"organization.updated"}, _org_update, _org_unknown, 404),
    ("DELETE", ORG_ID): ({"organization.deleted"}, _org_delete, _org_unknown_delete, 404),
    ("DELETE", f"{ORG_ID}/invitations/{{invitation_id}}"): (
        {"organization.invitation_revoked"},
        _inv_revoke,
        _inv_revoke_unknown,
        404,
    ),
    ("PATCH", USER): ({"user.disabled", "user.enabled"}, _user_status, _user_unknown, 404),
    ("DELETE", f"{USER}/sessions/{{session_id}}"): (
        {"user.session_revoked"},
        _session_revoke,
        _session_unknown,
        404,
    ),
    ("DELETE", f"{USER}/sessions"): (
        {"user.sessions_revoked"},
        _sessions_revoke_all,
        _sessions_unknown_user,
        404,
    ),
    ("POST", ASSIGN): ({"role_assignment.granted"}, _assign, _assign_unknown_user, 404),
    ("DELETE", f"{ASSIGN}/{{assignment_id}}"): (
        {"role_assignment.revoked"},
        _revoke,
        _revoke_unknown,
        404,
    ),
    ("POST", f"{V1}/apps"): ({"app.created"}, _app_create, _app_create_dup, 409),
    ("PATCH", APP): ({"app.updated"}, _app_update, _app_unknown, 404),
    ("DELETE", APP): ({"app.deleted"}, _app_delete, _app_unknown_delete, 404),
    ("POST", PERMS): ({"permission.created"}, _perm_create, _perm_create_dup, 409),
    ("PATCH", f"{PERMS}/{{permission_id}}"): (
        {"permission.updated"},
        _perm_update,
        _perm_unknown,
        404,
    ),
    ("DELETE", f"{PERMS}/{{permission_id}}"): (
        {"permission.deleted"},
        _perm_delete,
        _perm_unknown_delete,
        404,
    ),
    ("POST", ROLES): ({"role.created"}, _role_create, _role_create_dup, 409),
    ("PATCH", f"{ROLES}/{{role_id}}"): ({"role.updated"}, _role_update, _role_unknown, 404),
    ("DELETE", f"{ROLES}/{{role_id}}"): (
        {"role.deleted"},
        _role_delete,
        _role_unknown_delete,
        404,
    ),
    ("PUT", f"{ROLES}/{{role_id}}/permissions"): (
        {"role.permissions_replaced"},
        _role_permissions,
        _role_unknown_permissions,
        404,
    ),
    ("POST", CREDS): ({"app_credential.issued"}, _cred_issue, _cred_issue_unknown, 404),
    ("DELETE", f"{CREDS}/{{credential_id}}"): (
        {"app_credential.revoked"},
        _cred_revoke,
        _cred_revoke_unknown,
        404,
    ),
    ("POST", DEPLOYER): (
        {"deployer_credential.issued"},
        _deployer_issue,
        _deployer_issue_at_limit,
        409,
    ),
    ("DELETE", f"{DEPLOYER}/{{credential_id}}"): (
        {"deployer_credential.revoked"},
        _deployer_revoke,
        _deployer_revoke_unknown,
        404,
    ),
    ("POST", f"{JREQ}/{{request_id}}/approve"): (
        {"app.join_request_approved"},
        _jr_approve,
        _jr_unknown_approve,
        404,
    ),
    ("POST", f"{JREQ}/{{request_id}}/reject"): (
        {"app.join_request_rejected"},
        _jr_reject,
        _jr_unknown_reject,
        404,
    ),
    ("POST", ENT): ({"entitlement.granted"}, _ent_grant, _ent_grant_unknown_org, 404),
    ("PATCH", f"{ENT}/{{entitlement_id}}"): (
        {"entitlement.updated"},
        _ent_update,
        _ent_unknown_update,
        404,
    ),
    ("DELETE", f"{ENT}/{{entitlement_id}}"): (
        {"entitlement.revoked"},
        _ent_revoke,
        _ent_unknown_revoke,
        404,
    ),
    ("PATCH", f"{V1}/bug-reports/{{report_id}}"): (
        {"bug_report.marked_done", "bug_report.reopened", "bug_report.note_updated"},
        _bug_done,
        _bug_unknown,
        404,
    ),
    ("DELETE", f"{V1}/releases/{{release_id}}"): (
        {"release.deleted"},
        _release_delete,
        _release_unknown,
        404,
    ),
    ("POST", f"{V1}/apps/{{key}}/usage-metrics"): (
        {"usage_metric.created"},
        _usage_create,
        _usage_create_dup,
        409,
    ),
    ("PATCH", f"{V1}/apps/{{key}}/usage-metrics/{{metric_id}}"): (
        {"usage_metric.updated", "usage_metric.archived", "usage_metric.restored"},
        _usage_update,
        _usage_unknown,
        404,
    ),
}
_SUCCESS_STATUS = {"POST": 201, "PATCH": 200, "PUT": 200, "DELETE": 204}
_SUCCESS_STATUS_BY_ROUTE = {  # state transitions answer 200 with the updated resource
    ("POST", f"{JREQ}/{{request_id}}/approve"): 200,
    ("POST", f"{JREQ}/{{request_id}}/reject"): 200,
}


def _success_status(route: tuple[str, str]) -> int:
    return _SUCCESS_STATUS_BY_ROUTE.get(route, _SUCCESS_STATUS[route[0]])


@pytest.fixture
async def actor(db_session: AsyncSession) -> User:
    return await make_user(db_session)


async def _rows(db_session: AsyncSession) -> list[AuditLog]:
    result = await db_session.scalars(
        select(AuditLog)
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(result)


async def _count(db_session: AsyncSession) -> int:
    return await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0


def test_every_mutating_bearer_route_is_mapped() -> None:
    assert set(AUDITED) == {r for r in BEARER if r[0] != "GET"}
    assert len(AUDITED) == 32


@pytest.mark.parametrize("route", list(AUDITED), ids=lambda r: f"{r[0]} {r[1]}")
async def test_success_writes_one_row(
    make_client: MakeClient, db_session: AsyncSession, actor: User, route: tuple[str, str]
) -> None:
    method = route[0]
    actions, success, _, _ = AUDITED[route]
    url, body = await success(db_session)
    before = await _count(db_session)
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        resp = await client.request(method, url, json=body)
    assert resp.status_code == _success_status(route), resp.text
    assert await _count(db_session) == before + 1
    row = (await _rows(db_session))[-1]
    assert row.action in actions
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("user", actor.id, None)
    assert row.target_type is not None and row.target_id is not None
    assert row.changes


@pytest.mark.parametrize("route", list(AUDITED), ids=lambda r: f"{r[0]} {r[1]}")
async def test_failure_writes_no_row(
    make_client: MakeClient, db_session: AsyncSession, actor: User, route: tuple[str, str]
) -> None:
    """`make_client` never rolls back, so a row added before a guard would stay visible."""
    _, _, failure, status = AUDITED[route]
    url, body = await failure(db_session)
    before = await _count(db_session)
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        resp = await client.request(route[0], url, json=body)
    assert resp.status_code == status, resp.text
    assert await _count(db_session) == before


async def test_denied_guard_writes_row(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    """ADM-004b: a guard refusal writes one `outcome=denied` row (formerly none)."""
    holder = await make_user(db_session)
    holder.email_verified_at = datetime.now(UTC)
    ra = await assign(db_session, holder, await super_admin_role(db_session))
    url = f"{V1}/users/{holder.id}/role-assignments/{ra.id}"
    await db_session.commit()  # the denied write rolls back first: keep the setup
    before = await _count(db_session)
    async with make_client(admin_permissions=["admin:apps:write"], admin_user=actor) as client:
        resp = await client.patch(f"{V1}/apps/admin", json={"name": "Renamed"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin_wildcard_required"
    assert await _count(db_session) == before + 1

    await db_session.refresh(actor)  # expired by the denied request's rollback
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        resp = await client.delete(url)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "last_super_admin"
    rows = await _rows(db_session)
    assert len(rows) == before + 2
    denied = sorted((r.action, r.outcome) for r in rows if r.outcome == "denied")
    assert denied == [("app.updated", "denied"), ("role_assignment.revoked", "denied")]


async def test_session_self_revoke_writes_denied_row(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    """USR-10: staff cannot revoke their own sessions; each refusal writes one denied row."""
    _, _, (account,) = await make_device(db_session, actor)
    await db_session.commit()  # the denied write rolls back first: keep the setup
    before = await _count(db_session)
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        one = await client.delete(f"{V1}/users/{actor.id}/sessions/{account.id}")
        assert one.status_code == 403
        assert one.json()["error"]["code"] == "self_session_change_forbidden"
        await db_session.refresh(actor)  # expired by the denied request's rollback
        every = await client.delete(f"{V1}/users/{actor.id}/sessions")
        assert every.status_code == 403
        assert every.json()["error"]["code"] == "self_session_change_forbidden"
    rows = (await _rows(db_session))[before:]
    assert sorted((r.action, r.outcome) for r in rows) == [
        ("user.session_revoked", "denied"),
        ("user.sessions_revoked", "denied"),
    ]
    assert all(r.changes == {"reason": "self_session_change_forbidden"} for r in rows)
    await db_session.refresh(account)
    assert account.revoked_at is None


async def test_session_revoke_noops_write_no_row(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    user = await make_user(db_session)
    _, _, (account,) = await make_device(db_session, user)
    other = await make_user(db_session)  # no sessions
    account.revoked_at = datetime.now(UTC)
    await db_session.flush()
    before = await _count(db_session)
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        none = await client.delete(f"{V1}/users/{other.id}/sessions")
        assert none.status_code == 204
        done = await client.delete(f"{V1}/users/{user.id}/sessions/{account.id}")
        assert done.status_code == 404
    assert await _count(db_session) == before


async def test_noop_writes_no_row(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    app, role = await _role(db_session)
    perm = await make_permission(db_session, app, f"{app.key}:items:read")
    await grant(db_session, role, perm)
    user = await make_user(db_session)
    calls = [
        ("PATCH", f"{V1}/apps/{app.key}", {"name": app.name}),
        ("PATCH", f"{V1}/users/{user.id}", {"status": "active"}),
        ("PATCH", f"{V1}/apps/{app.key}/roles/{role.id}", {"name": role.name}),
        ("PATCH", f"{V1}/apps/{app.key}/permissions/{perm.id}", {"is_admin": False}),
        (
            "PUT",
            f"{V1}/apps/{app.key}/roles/{role.id}/permissions",
            {"permission_keys": [perm.key]},
        ),
    ]
    before = await _count(db_session)
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        for method, url, body in calls:
            resp = await client.request(method, url, json=body)
            assert resp.status_code == 200, (url, resp.text)
    assert await _count(db_session) == before


async def test_strict_rollback_leaves_no_row(
    make_strict_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    taken = await _org(db_session)
    slug, actor_id = taken.slug, actor.id
    await db_session.commit()
    before = await _count(db_session)
    async with make_strict_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        ok = await client.post(f"{V1}/organizations", json={"slug": "org-strict", "name": "S"})
        assert ok.status_code == 201, ok.text
        assert await _count(db_session) == before + 1
        dup = await client.post(f"{V1}/organizations", json={"slug": slug, "name": "S"})
        assert dup.status_code == 409
    assert await _count(db_session) == before + 1
    row = (await _rows(db_session))[-1]
    assert (row.action, row.actor_user_id) == ("organization.created", actor_id)


async def test_row_carries_request_context(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    headers = {"X-Request-ID": "req-abc.123_X", "User-Agent": "audit-test/1.0"}
    async with make_client(
        admin_permissions=["admin:*"], admin_user=actor, trusted_proxy_cidrs=["127.0.0.0/8"]
    ) as client:
        resp = await client.post(
            f"{V1}/organizations", json={"slug": "org-ctx", "name": "Ctx"}, headers=headers
        )
    assert resp.status_code == 201
    echoed = resp.headers["x-request-id"]
    assert echoed != "req-abc.123_X"
    assert uuid.UUID(echoed).version == 4
    row = (await _rows(db_session))[-1]
    assert (row.request_id, row.user_agent, str(row.ip)) == (
        echoed,
        "audit-test/1.0",
        "127.0.0.1",
    )
    assert (row.target_type, row.target_id, row.org_id) == (
        "organization",
        uuid.UUID(resp.json()["id"]),
        uuid.UUID(resp.json()["id"]),
    )
    assert row.changes is not None
    assert row.changes["slug"] == {"from": None, "to": "org-ctx"}


async def test_no_secret_or_email_in_any_row(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        for route, (_, success, _, _) in AUDITED.items():
            url, body = await success(db_session)
            resp = await client.request(route[0], url, json=body)
            assert resp.status_code == _success_status(route), (url, resp.text)
    rows = await _rows(db_session)
    assert len(rows) >= len(AUDITED)
    emails = list(await db_session.scalars(select(User.email)))
    for row in rows:
        text = f"{row.changes} {row.user_agent} {row.actor_label}"
        for needle in ("_hash", "token", "password", "cookie", "@", *emails):
            assert needle not in text, (row.action, needle)


# --- identity routes (ADM-004b) ------------------------------------------------------

IdCall = tuple[str, dict[str, Any], dict[str, str]]  # (url, json body, headers)
IdScenario = Callable[[AsyncSession], Awaitable[IdCall]]
AUTH = f"{V1}/auth"


async def _id_login(s: AsyncSession) -> IdCall:
    await make_login_app(s)
    user = await make_login_user(s)
    return LOGIN, {"email": user.email, "password": PASSWORD}, auth_headers()


async def _id_logout(s: AsyncSession) -> IdCall:
    await make_login_app(s)
    raw, _, (account,) = await make_device(s, await make_login_user(s))
    return f"{AUTH}/logout", {"account_id": str(account.id)}, auth_headers(cookie=raw)


async def _id_join(s: AsyncSession) -> IdCall:
    # The `approval` (pending) path writes one `app.join_requested` row: test_join_requests_join.py
    await make_member_role(s, await make_login_app(s))
    raw, _, (account,) = await make_device(s, await make_login_user(s))
    body = {"account_id": str(account.id)}
    return f"{AUTH}/apps/stoktok/join", body, auth_headers(cookie=raw)


async def _id_report(s: AsyncSession) -> IdCall:
    app = await make_login_app(s)
    role = await make_member_role(s, app)
    user = await make_login_user(s)
    await assign(s, user, role)
    raw, _, (account,) = await make_device(s, user)
    body = {"account_id": str(account.id), "description": "broken"}
    return f"{AUTH}/bug-reports", body, auth_headers(cookie=raw)


async def _id_token(s: AsyncSession, purpose: str) -> tuple[uuid.UUID, str]:
    user = await make_login_user(s, verified=False)
    raw = uuid.uuid4().hex + uuid.uuid4().hex
    ttl = RESET_TTL if purpose == RESET else VERIFY_TTL
    await EmailTokenRepository(s).issue(user.id, purpose, sha256(raw), ttl)
    return user.id, raw


async def _id_verify(s: AsyncSession) -> IdCall:
    _, raw = await _id_token(s, VERIFY)
    return f"{AUTH}/verify-email", {"token": raw}, {}


async def _id_reset(s: AsyncSession) -> IdCall:
    _, raw = await _id_token(s, RESET)
    return f"{AUTH}/password/reset", {"token": raw, "new_password": "a brand new phrase 42"}, {}


# route -> (action, success scenario, success status)
IDENTITY_AUDITED: dict[tuple[str, str], tuple[str, IdScenario, int]] = {
    ("POST", f"{AUTH}/login"): ("auth.login_succeeded", _id_login, 200),
    ("POST", f"{AUTH}/logout"): ("auth.logged_out", _id_logout, 204),
    ("POST", f"{AUTH}/apps/{{key}}/join"): ("app.joined", _id_join, 200),
    ("POST", f"{AUTH}/bug-reports"): ("bug_report.created", _id_report, 201),
    ("POST", f"{AUTH}/verify-email"): ("auth.email_verified", _id_verify, 204),
    ("POST", f"{AUTH}/password/reset"): ("auth.password_reset_completed", _id_reset, 204),
}
# Not audited (ADR-0009 amendment): sign-up and token issuance (follow-up), resend, forgot.
IDENTITY_NOT_AUDITED = {
    ("POST", f"{AUTH}/signup"),
    ("POST", f"{AUTH}/token"),
    ("POST", f"{AUTH}/verify-email/resend"),
    ("POST", f"{AUTH}/password/forgot"),
}
# Audited with several rows, which the one-row harness above cannot express: `POST /auth/orgs`
# writes organization.created + role_assignment.granted (covered by tests/test_auth_orgs.py);
# the two invitation routes (covered by tests/test_invitations_*.py); the attachment upload, whose
# raw body and Drive call the JSON harness above cannot send (writes `bug_report.attachment_added`,
# covered by tests/test_bug_report_attachments_upload.py).
IDENTITY_MULTI_ROW_AUDITED = {
    ("POST", f"{AUTH}/bug-reports/{{report_id}}/attachments"),
    ("POST", f"{AUTH}/orgs"),
    ("POST", f"{AUTH}/orgs/{{org_id}}/invitations"),
    ("POST", f"{AUTH}/invitations/accept"),
}


def test_every_mutating_identity_route_is_classified() -> None:
    assert set(IDENTITY_AUDITED).isdisjoint(IDENTITY_NOT_AUDITED)
    assert IDENTITY_MULTI_ROW_AUDITED.isdisjoint(IDENTITY_NOT_AUDITED)
    assert IDENTITY_MULTI_ROW_AUDITED.isdisjoint(IDENTITY_AUDITED)
    assert set(IDENTITY_AUDITED) | IDENTITY_MULTI_ROW_AUDITED | IDENTITY_NOT_AUDITED == {
        r for r in PUBLIC | COOKIE if r[0] != "GET"
    }


@pytest.mark.parametrize("route", list(IDENTITY_AUDITED), ids=lambda r: f"{r[0]} {r[1]}")
async def test_identity_success_writes_one_row(
    make_strict_client: MakeClient, db_session: AsyncSession, route: tuple[str, str]
) -> None:
    action, scenario, status = IDENTITY_AUDITED[route]
    url, body, headers = await scenario(db_session)
    await db_session.commit()
    before = await _count(db_session)
    async with make_strict_client() as client:
        resp = await client.request(route[0], url, json=body, headers=headers)
    assert resp.status_code == status, resp.text
    assert await _count(db_session) == before + 1
    row = (await _rows(db_session))[-1]
    assert (row.action, row.outcome) == (action, "success")


# --- app credential routes (USR-05b, ADR-0016) ------------------------------

APP_CREDENTIAL_AUDITED = {("PUT", f"{V1}/apps/{{key}}/manifest"): {"app.manifest_synced"}}


def test_app_credential_routes_are_classified() -> None:
    assert set(APP_CREDENTIAL_AUDITED) == APP_CREDENTIAL


async def test_app_credential_routes_are_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await _app(db_session)
    _, raw = await make_credential(db_session, app)
    headers = {"Authorization": f"Bearer {raw}"}
    url = f"{V1}/apps/{app.key}/manifest"
    before = await _count(db_session)
    bad = await client.put(url, json={"permissions": [{"key": "other:x:read"}]}, headers=headers)
    assert bad.status_code == 422
    assert await _count(db_session) == before
    ok = await client.put(
        url, json={"permissions": [{"key": f"{app.key}:x:read"}]}, headers=headers
    )
    assert ok.status_code == 200
    rows = (await _rows(db_session))[before:]
    assert [r.action for r in rows] == ["app.manifest_synced"]
    assert {r.action for r in rows} <= next(iter(APP_CREDENTIAL_AUDITED.values()))


async def test_user_disable_with_pending_requests_writes_multiple_rows(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    user = await make_user(db_session)
    for _ in range(2):
        await make_join_request(db_session, await make_approval_app(db_session), user)
    before = await _count(db_session)
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        resp = await client.patch(f"{V1}/users/{user.id}", json={"status": "disabled"})
    assert resp.status_code == 200, resp.text
    assert await _count(db_session) == before + 3
    rows = [r for r in await _rows(db_session) if r.actor_user_id == actor.id]
    assert sorted(r.action for r in rows) == [
        "app.join_request_cancelled",
        "app.join_request_cancelled",
        "user.disabled",
    ]
    for row in rows:
        assert (row.outcome, row.actor_user_id) == ("success", actor.id)
        assert "@" not in str(row.changes)
