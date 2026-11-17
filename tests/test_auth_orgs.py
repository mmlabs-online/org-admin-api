"""POST/GET /api/v1/auth/orgs (USR-06a): create as owner, list mine, limits, audit."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.sessions.models import AccountSession
from app.modules.users.models import User
from tests.auth_factories import auth_headers, make_device, make_login_app, make_login_user
from tests.conftest import MakeClient
from tests.rbac_factories import assign, grant, make_org, make_permission, make_role

ORGS = "/api/v1/auth/orgs"
CREATE_PERM = "calechip:org:create"


def _error(resp: Any) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


def _headers(raw: str) -> dict[str, str]:
    return auth_headers(app_key="calechip", cookie=raw)


def _body(account: AccountSession, slug: str = "acme", **extra: Any) -> dict[str, Any]:
    return {"account_id": str(account.id), "slug": slug, "name": "Acme", **extra}


async def _calechip(db_session: AsyncSession, *, owner: bool = True) -> tuple[App, Role, Role]:
    """`calechip` (org_mode required): default `member` holding `org:create`, plus an owner role."""
    app = await make_login_app(db_session, "calechip", org_mode="required")
    member = await make_role(db_session, app, "calechip:member")
    await grant(db_session, member, await make_permission(db_session, app, CREATE_PERM))
    owner_role = await make_role(db_session, app, "calechip:org-owner")
    await grant(db_session, owner_role, await make_permission(db_session, app, "calechip:org:edit"))
    app.default_role_id = member.id
    if owner:
        app.org_owner_role_id = owner_role.id
    await db_session.flush()
    return app, member, owner_role


async def _signed_in(
    db_session: AsyncSession, role: Role | None, **user_kwargs: Any
) -> tuple[User, str, AccountSession]:
    user = await make_login_user(db_session, **user_kwargs)
    if role is not None:
        await assign(db_session, user, role)
    raw, _, (account,) = await make_device(db_session, user)
    return user, raw, account


async def _audit(db_session: AsyncSession) -> list[AuditLog]:
    rows = await db_session.scalars(select(AuditLog).order_by(AuditLog.occurred_at, AuditLog.id))
    return [r for r in rows if r.action in {"organization.created", "role_assignment.granted"}]


# --- create ------------------------------------------------------------------


async def test_create_org_grants_owner_role_in_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, _, owner = await _calechip(db_session)
    user, raw, account = await _signed_in(db_session, (await _calechip_member(db_session)))
    resp = await client.post(ORGS, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "acme" and body["status"] == "active"
    assert body["roles"] == ["calechip:org-owner"]
    assert resp.headers["cache-control"] == "no-store"

    org = await db_session.get(Organization, uuid.UUID(body["id"]))
    assert org is not None and org.created_by == user.id
    grants = list(
        await db_session.scalars(select(RoleAssignment).where(RoleAssignment.org_id == org.id))
    )
    assert [(g.user_id, g.role_id, g.granted_by) for g in grants] == [(user.id, owner.id, None)]


async def _calechip_member(db_session: AsyncSession) -> Role:
    role = await db_session.scalar(select(Role).where(Role.key == "calechip:member"))
    assert role is not None
    return role


async def test_create_org_requires_org_create_permission_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, member, _ = await _calechip(db_session)
    plain = await make_role(db_session, app, "calechip:plain")
    _, raw, account = await _signed_in(db_session, plain)
    resp = await client.post(ORGS, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_create_forbidden"
    assert member is not None


async def test_create_org_permission_held_only_in_an_org_does_not_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, member, _ = await _calechip(db_session)
    plain = await make_role(db_session, app, "calechip:plain")
    user, raw, account = await _signed_in(db_session, plain)
    await assign(db_session, user, member, org_id=(await make_org(db_session)).id)
    resp = await client.post(ORGS, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_create_forbidden"


async def test_create_org_not_member_403(client: AsyncClient, db_session: AsyncSession) -> None:
    await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, None)
    resp = await client.post(ORGS, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "not_member"
    assert _error(resp)["details"] == {"join_available": True}


async def test_create_org_no_owner_role_configured_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, member, _ = await _calechip(db_session, owner=False)
    _, raw, account = await _signed_in(db_session, member)
    resp = await client.post(ORGS, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_create_unavailable"


async def test_create_org_owner_role_with_admin_perm_403(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        logging.getLogger("app.modules.organizations.auth_service"), "disabled", False
    )
    app, member, owner = await _calechip(db_session)
    await grant(
        db_session, owner, await make_permission(db_session, app, "calechip:x:y", is_admin=True)
    )
    _, raw, account = await _signed_in(db_session, member)
    with caplog.at_level(logging.WARNING):
        resp = await client.post(ORGS, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_create_unavailable"
    assert "org_owner_role_unusable" in caplog.text


async def test_create_org_slug_taken_409(client: AsyncClient, db_session: AsyncSession) -> None:
    _, member, _ = await _calechip(db_session)
    await make_org(db_session, slug="acme")
    _, raw, account = await _signed_in(db_session, member)
    resp = await client.post(ORGS, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 409
    assert _error(resp)["code"] == "conflict"
    assert await _audit(db_session) == []


@pytest.mark.parametrize(
    "extra",
    [
        {"slug": "Not Valid!"},
        {"slug": ""},
        {"name": ""},
        {"unknown": 1},
        {"description": "x" * 5001},
    ],
)
async def test_create_org_validation_422(
    client: AsyncClient, db_session: AsyncSession, extra: dict[str, Any]
) -> None:
    _, member, _ = await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, member)
    resp = await client.post(ORGS, json=_body(account) | extra, headers=_headers(raw))
    assert resp.status_code == 422
    assert _error(resp)["code"] == "validation_error"


async def test_create_org_on_org_less_app_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    stoktok = await make_login_app(db_session)
    viewer = await make_role(db_session, stoktok, "stoktok:viewer")
    _, raw, account = await _signed_in(db_session, viewer)
    resp = await client.post(ORGS, json=_body(account), headers=auth_headers(cookie=raw))
    assert resp.status_code == 422
    assert _error(resp)["code"] == "org_not_supported"


async def test_create_org_unverified_403(client: AsyncClient, db_session: AsyncSession) -> None:
    _, member, _ = await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, member, verified=False)
    resp = await client.post(ORGS, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "email_unverified"


async def test_create_org_bad_account_401(client: AsyncClient, db_session: AsyncSession) -> None:
    _, member, _ = await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, member)
    body = _body(account) | {"account_id": str(uuid.uuid4())}
    resp = await client.post(ORGS, json=body, headers=_headers(raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"


async def test_create_org_per_user_cap_403(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    _, member, _ = await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, member)
    async with make_client(org_create_max_per_user=1) as client:
        first = await client.post(ORGS, json=_body(account, "one"), headers=_headers(raw))
        assert first.status_code == 201, first.text
        second = await client.post(ORGS, json=_body(account, "two"), headers=_headers(raw))
    assert second.status_code == 403
    assert _error(second)["code"] == "org_limit_reached"


async def test_create_org_rate_limited_429(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    _, member, _ = await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, member)
    async with make_client(org_create_rate_limit=1) as client:
        await client.post(ORGS, json=_body(account, "one"), headers=_headers(raw))
        second = await client.post(ORGS, json=_body(account, "two"), headers=_headers(raw))
    assert second.status_code == 429
    assert _error(second)["code"] == "rate_limited"


async def test_create_org_audit_rows(client: AsyncClient, db_session: AsyncSession) -> None:
    app, member, owner = await _calechip(db_session)
    user, raw, account = await _signed_in(db_session, member)
    resp = await client.post(ORGS, json=_body(account, description="d"), headers=_headers(raw))
    assert resp.status_code == 201
    org_id = uuid.UUID(resp.json()["id"])
    rows = {r.action: r for r in await _audit(db_session)}  # one transaction: same timestamp
    assert set(rows) == {"organization.created", "role_assignment.granted"}
    created, granted = rows["organization.created"], rows["role_assignment.granted"]
    for row in (created, granted):
        assert (row.outcome, row.actor_kind, row.actor_user_id) == ("success", "user", user.id)
        assert (row.app_key, row.org_id) == ("calechip", org_id)
    assert created.target_id == org_id
    assert created.changes == {
        "slug": {"from": None, "to": "acme"},
        "name": {"from": None, "to": "Acme"},
        "description": {"from": None, "to": "d"},
        "status": {"from": None, "to": "active"},
        "created_by": {"from": None, "to": str(user.id)},
    }
    assert granted.changes == {
        "role_key": {"from": None, "to": "calechip:org-owner"},
        "org_id": {"from": None, "to": str(org_id)},
    }
    text = f"{created.changes} {granted.changes}"
    assert user.email not in text and "@" not in text
    assert app.key == "calechip" and owner.key in text


async def test_create_org_refusal_writes_no_audit(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, _, _ = await _calechip(db_session)
    plain = await make_role(db_session, app, "calechip:plain")
    _, raw, account = await _signed_in(db_session, plain)
    resp = await client.post(ORGS, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert await _audit(db_session) == []
    assert await db_session.scalar(select(Organization).where(Organization.slug == "acme")) is None


# --- list --------------------------------------------------------------------


async def _list(client: AsyncClient, account: AccountSession, raw: str, **params: Any) -> Any:
    return await client.get(
        ORGS, params={"account_id": str(account.id), **params}, headers=_headers(raw)
    )


async def test_list_my_orgs_only_calling_app(client: AsyncClient, db_session: AsyncSession) -> None:
    app, member, owner = await _calechip(db_session)
    editor = await make_role(db_session, app, "calechip:editor")
    mine, other = await make_org(db_session, name="Mine"), await make_org(db_session, name="Other")
    user, raw, account = await _signed_in(db_session, member)
    await assign(db_session, user, owner, org_id=mine.id)
    await assign(db_session, user, editor, org_id=mine.id)
    resp = await _list(client, account, raw)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"] == [
        {
            "id": str(mine.id),
            "slug": mine.slug,
            "name": "Mine",
            "status": "active",
            "roles": ["calechip:editor", "calechip:org-owner"],
        }
    ]
    assert str(other.id) not in resp.text
    assert resp.headers["cache-control"] == "no-store"


async def test_list_excludes_orgs_with_only_other_app_roles(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, member, _ = await _calechip(db_session)
    stoktok = await make_login_app(db_session)
    foreign = await make_role(db_session, stoktok, "stoktok:viewer")
    org = await make_org(db_session)
    user, raw, account = await _signed_in(db_session, member)
    await assign(db_session, user, foreign, org_id=org.id)
    resp = await _list(client, account, raw)
    assert resp.status_code == 200
    assert resp.json()["items"] == [] and resp.json()["total"] == 0


async def test_list_excludes_expired(client: AsyncClient, db_session: AsyncSession) -> None:
    _, member, owner = await _calechip(db_session)
    org = await make_org(db_session)
    user, raw, account = await _signed_in(db_session, member)
    await assign(
        db_session, user, owner, org_id=org.id, expires_at=datetime(2000, 1, 1, tzinfo=UTC)
    )
    resp = await _list(client, account, raw)
    assert resp.json()["items"] == []


async def test_list_includes_suspended_with_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, member, owner = await _calechip(db_session)
    org = await make_org(db_session, status="suspended")
    user, raw, account = await _signed_in(db_session, member)
    await assign(db_session, user, owner, org_id=org.id)
    resp = await _list(client, account, raw)
    assert [i["status"] for i in resp.json()["items"]] == ["suspended"]


async def test_list_app_scope_only_user_gets_empty_page(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, member, _ = await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, member)
    resp = await _list(client, account, raw)
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


async def test_list_paginates(client: AsyncClient, db_session: AsyncSession) -> None:
    _, member, owner = await _calechip(db_session)
    user, raw, account = await _signed_in(db_session, member)
    for name in ("C", "A", "B"):
        await assign(db_session, user, owner, org_id=(await make_org(db_session, name=name)).id)
    first = (await _list(client, account, raw, limit=2)).json()
    assert (first["total"], [i["name"] for i in first["items"]]) == (3, ["A", "B"])
    second = (await _list(client, account, raw, limit=2, offset=2)).json()
    assert [i["name"] for i in second["items"]] == ["C"]


async def test_list_on_org_less_app_422(client: AsyncClient, db_session: AsyncSession) -> None:
    stoktok = await make_login_app(db_session)
    viewer = await make_role(db_session, stoktok, "stoktok:viewer")
    _, raw, account = await _signed_in(db_session, viewer)
    resp = await client.get(
        ORGS, params={"account_id": str(account.id)}, headers=auth_headers(cookie=raw)
    )
    assert resp.status_code == 422
    assert _error(resp)["code"] == "org_not_supported"


async def test_list_requires_account_id_422(client: AsyncClient, db_session: AsyncSession) -> None:
    await _calechip(db_session)
    _, raw, _ = await _signed_in(db_session, None)
    resp = await client.get(ORGS, headers=_headers(raw))
    assert resp.status_code == 422


async def test_list_unverified_403(client: AsyncClient, db_session: AsyncSession) -> None:
    _, member, _ = await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, member, verified=False)
    resp = await _list(client, account, raw)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "email_unverified"


async def test_list_bad_account_401(client: AsyncClient, db_session: AsyncSession) -> None:
    _, member, _ = await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, member)
    resp = await client.get(ORGS, params={"account_id": str(uuid.uuid4())}, headers=_headers(raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert account is not None


async def test_list_rate_limited_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    _, member, _ = await _calechip(db_session)
    _, raw, account = await _signed_in(db_session, member)
    async with make_client(orgs_list_rate_limit=1) as client:
        await _list(client, account, raw)
        second = await _list(client, account, raw)
    assert second.status_code == 429
