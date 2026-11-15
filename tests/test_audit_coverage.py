"""Every mutating Bearer route writes exactly one audit row on success, none on failure (ADM-004).

`test_every_mutating_bearer_route_is_mapped` fails for a new mutating route until it is added to
`AUDITED` with a success and a failure scenario; `test_every_mutating_identity_route_is_classified`
does the same for identity routes (ADM-004b): audited (with a success scenario) or not.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.auth.email_verification import VERIFY, VERIFY_TTL
from app.modules.auth.password_reset import RESET, RESET_TTL
from app.modules.auth.repository import EmailTokenRepository
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Permission, Role
from app.modules.users.models import User
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
from tests.rbac_factories import (
    assign,
    grant,
    make_app,
    make_permission,
    make_role,
    make_user,
    super_admin_role,
)
from tests.test_route_auth_classes import BEARER, COOKIE, PUBLIC

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
    return f"{V1}/users/{(await make_user(s)).id}", {"status": "disabled"}


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


# --- failure scenarios (404 unknown id/key, 409 duplicate) -------------------


async def _org_create_dup(s: AsyncSession) -> Call:
    return f"{V1}/organizations", {"slug": (await _org(s)).slug, "name": "Org"}


async def _org_unknown(s: AsyncSession) -> Call:
    return f"{V1}/organizations/{uuid.uuid4()}", {"name": "Renamed"}


async def _org_unknown_delete(s: AsyncSession) -> Call:
    return f"{V1}/organizations/{uuid.uuid4()}", None


async def _user_unknown(s: AsyncSession) -> Call:
    return f"{V1}/users/{uuid.uuid4()}", {"status": "disabled"}


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


async def _role_unknown(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/roles/{uuid.uuid4()}", {"name": "Renamed"}


async def _role_unknown_delete(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/roles/{uuid.uuid4()}", None


async def _role_unknown_permissions(s: AsyncSession) -> Call:
    app = await _app(s)
    return f"{V1}/apps/{app.key}/roles/{uuid.uuid4()}/permissions", {"permission_keys": []}


ORG, ORG_ID = f"{V1}/organizations", f"{V1}/organizations/{{org_id}}"
USER, ASSIGN = f"{V1}/users/{{user_id}}", f"{V1}/users/{{user_id}}/role-assignments"
APP, PERMS, ROLES = (
    f"{V1}/apps/{{key}}",
    f"{V1}/apps/{{key}}/permissions",
    f"{V1}/apps/{{key}}/roles",
)

# route -> (allowed actions, success scenario, failure scenario, failure status)
AUDITED: dict[tuple[str, str], tuple[set[str], Scenario, Scenario, int]] = {
    ("POST", ORG): ({"organization.created"}, _org_create, _org_create_dup, 409),
    ("PATCH", ORG_ID): ({"organization.updated"}, _org_update, _org_unknown, 404),
    ("DELETE", ORG_ID): ({"organization.deleted"}, _org_delete, _org_unknown_delete, 404),
    ("PATCH", USER): ({"user.disabled", "user.enabled"}, _user_status, _user_unknown, 404),
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
}
_SUCCESS_STATUS = {"POST": 201, "PATCH": 200, "PUT": 200, "DELETE": 204}


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
    assert len(AUDITED) == 16


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
    assert resp.status_code == _SUCCESS_STATUS[method], resp.text
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
        for (method, _), (_, success, _, _) in AUDITED.items():
            url, body = await success(db_session)
            resp = await client.request(method, url, json=body)
            assert resp.status_code == _SUCCESS_STATUS[method], (url, resp.text)
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
    await make_member_role(s, await make_login_app(s))
    raw, _, (account,) = await make_device(s, await make_login_user(s))
    body = {"account_id": str(account.id)}
    return f"{AUTH}/apps/stoktok/join", body, auth_headers(cookie=raw)


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


def test_every_mutating_identity_route_is_classified() -> None:
    assert set(IDENTITY_AUDITED).isdisjoint(IDENTITY_NOT_AUDITED)
    assert set(IDENTITY_AUDITED) | IDENTITY_NOT_AUDITED == {
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
