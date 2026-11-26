"""Org token for a non-member org via the app's super-admin role (USR-39, ADR-0030 §7)."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.audit.service import AuditLogger
from app.modules.entitlements.models import OrgEntitlement
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.sessions.models import AccountSession
from app.modules.users.models import User
from tests.auth_factories import (
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    make_member_role,
)
from tests.conftest import TEST_JWT_ISSUER, MakeClient
from tests.jwt_verifier import verify_reference
from tests.rbac_factories import assign, make_org, make_role, super_admin_role

TOKEN = "/api/v1/auth/token"
JWKS = "/.well-known/jwks.json"
ACTION = "token.super_admin_org_issued"
DENIED = (
    b'{"error":{"code":"org_access_denied","message":"No access to this organization",'
    b'"details":{}}}'
)
PAST = datetime.now(UTC) - timedelta(days=1)


@dataclass
class World:
    app: App
    designated: Role
    member: Role
    holder: User
    raw: str
    account: AccountSession
    org: Organization
    designated_assignment: RoleAssignment
    admin_assignment: RoleAssignment

    def body(self, org_id: uuid.UUID | None = None) -> dict[str, Any]:
        return {
            "audience": "orgapp",
            "account_id": str(self.account.id),
            "org_id": str(org_id or self.org.id),
        }

    def headers(self) -> dict[str, str]:
        return auth_headers(app_key="orgapp", cookie=self.raw)


async def _entitled_org(db: AsyncSession, app: App, **kwargs: Any) -> Organization:
    org = await make_org(db, **kwargs)
    db.add(OrgEntitlement(org_id=org.id, app_id=app.id, plan="pro", limits={}))
    await db.flush()
    return org


async def _world(db: AsyncSession, *, designate: bool = True) -> World:
    app = await make_login_app(db, "orgapp", org_mode="required")
    member = await make_member_role(db, app)
    designated = await make_role(db, app, "orgapp:super-admin")
    if designate:
        app.super_admin_role_id = designated.id
        await db.flush()
    holder = await make_login_user(db)
    designated_assignment = await assign(db, holder, designated)
    admin_assignment = await assign(db, holder, await super_admin_role(db))
    raw, _, (account,) = await make_device(db, holder)
    org = await _entitled_org(db, app)
    return World(
        app, designated, member, holder, raw, account, org, designated_assignment, admin_assignment
    )


async def _ordinary(db: AsyncSession, world: World) -> tuple[str, AccountSession]:
    """A signed-in account that is an app member only."""
    user = await make_login_user(db)
    await assign(db, user, world.member)
    raw, _, (account,) = await make_device(db, user)
    return raw, account


async def _rows(db: AsyncSession, org_id: uuid.UUID) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.action == ACTION, AuditLog.org_id == org_id)
    return list((await db.scalars(stmt)).all())


def _claims(token: str, jwks: dict[str, Any]) -> dict[str, Any]:
    return verify_reference(token, jwks, audience="orgapp", issuer=TEST_JWT_ISSUER)


async def test_holder_non_member_org_claim_snapshot(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    other_app = await make_login_app(db_session, "thirdapp")
    await assign(db_session, w.holder, await make_role(db_session, other_app, "thirdapp:x"))
    async with make_client(access_token_ttl_seconds=43200) as client:
        resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
        assert resp.status_code == 200, resp.text
        jwks = (await client.get(JWKS)).json()
    body = resp.json()
    assert body["expires_in"] == 3600
    claims = _claims(body["access_token"], jwks)
    assert set(claims) == {
        "iss",
        "aud",
        "sub",
        "email",
        "email_verified",
        "roles",
        "org",
        "org_roles",
        "ent",
        "org_access",
        "iat",
        "exp",
        "jti",
    }
    assert claims["org"] == str(w.org.id)
    assert claims["org_roles"] == []
    assert claims["org_access"] == "super_admin"
    assert claims["roles"] == ["orgapp:super-admin"]
    assert claims["exp"] - claims["iat"] == 3600


async def test_holder_token_ent_entitled(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await _world(db_session)
    async with make_client(access_token_ttl_seconds=43200) as client:
        resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert claims["ent"] == {"plan": "pro", "limits": {}}


async def test_holder_token_ent_null_when_org_of_app_by_assignment_only(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await make_org(db_session)
    other = await make_login_user(db_session)
    await assign(db_session, other, w.member, org_id=org.id)
    async with make_client(access_token_ttl_seconds=43200) as client:
        resp = await client.post(TOKEN, json=w.body(org.id), headers=w.headers())
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert claims["ent"] is None
    assert claims["org_access"] == "super_admin"


async def test_holder_member_path_wins(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await _world(db_session)
    owner = await make_role(db_session, w.app, "orgapp:org-owner")
    await assign(db_session, w.holder, owner, org_id=w.org.id)
    async with make_client(access_token_ttl_seconds=43200) as client:
        resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["expires_in"] == 43200
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert claims["org_roles"] == ["orgapp:org-owner"]
    assert "org_access" not in claims
    assert await _rows(db_session, w.org.id) == []


async def test_holder_unknown_org_denied(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await _world(db_session)
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(uuid.uuid4()), headers=w.headers())
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "org_access_denied"


async def test_holder_suspended_org_denied(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await _entitled_org(db_session, w.app, status="suspended")
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(org.id), headers=w.headers())
    assert resp.status_code == 403
    assert resp.content == DENIED


async def test_holder_org_of_other_app_denied(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    other_app = await make_login_app(db_session, "thirdapp")
    org = await _entitled_org(db_session, other_app)
    user = await make_login_user(db_session)
    role = await make_role(db_session, other_app, "thirdapp:x")
    await assign(db_session, user, role, org_id=org.id)
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(org.id), headers=w.headers())
    assert resp.status_code == 403
    assert resp.content == DENIED


async def test_holder_org_with_only_expired_assignments_denied(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await make_org(db_session)
    other = await make_login_user(db_session)
    await assign(db_session, other, w.member, org_id=org.id, expires_at=PAST)
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(org.id), headers=w.headers())
    assert resp.status_code == 403
    assert resp.content == DENIED


async def test_holder_org_with_disabled_entitlement_only_denied(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await make_org(db_session)
    db_session.add(
        OrgEntitlement(org_id=org.id, app_id=w.app.id, plan="pro", limits={}, enabled=False)
    )
    await db_session.flush()
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(org.id), headers=w.headers())
    assert resp.status_code == 403
    assert resp.content == DENIED


@pytest.mark.parametrize(
    "scenario",
    [
        "non_holder_real_org",
        "non_holder_unknown_org",
        "holder_unknown_org",
        "holder_suspended_org",
        "holder_other_app_org",
        "app_without_designation",
        "holder_without_admin_super_admin",
    ],
)
async def test_denied_bodies_byte_identical(
    make_client: MakeClient, db_session: AsyncSession, scenario: str
) -> None:
    w = await _world(db_session, designate=scenario != "app_without_designation")
    org_id, raw, account = w.org.id, w.raw, w.account
    if scenario.startswith("non_holder"):
        raw, account = await _ordinary(db_session, w)
    if scenario.endswith("unknown_org"):
        org_id = uuid.uuid4()
    elif scenario == "holder_suspended_org":
        org_id = (await _entitled_org(db_session, w.app, status="suspended")).id
    elif scenario == "holder_other_app_org":
        org_id = (await _entitled_org(db_session, await make_login_app(db_session, "thirdapp"))).id
    elif scenario == "holder_without_admin_super_admin":
        await db_session.delete(w.admin_assignment)
        await db_session.flush()
    body = {"audience": "orgapp", "account_id": str(account.id), "org_id": str(org_id)}
    async with make_client() as client:
        resp = await client.post(TOKEN, json=body, headers=auth_headers("orgapp", cookie=raw))
    assert resp.status_code == 403
    assert resp.content == DENIED
    assert await _rows(db_session, org_id) == []


async def test_non_app_scope_or_expired_holder_is_non_holder(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    await assign(db_session, w.holder, w.member)  # still an app member: no `not_member`
    w.designated_assignment.expires_at = PAST
    await db_session.flush()
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
    assert resp.status_code == 403
    assert resp.content == DENIED


@pytest.mark.parametrize(
    "source",
    [
        "designated_assignment_revoked",
        "designated_assignment_expired",
        "designation_cleared",
        "admin_super_admin_revoked",
        "admin_super_admin_expired",
    ],
)
async def test_revocation_blocks_next_mint(
    make_client: MakeClient, db_session: AsyncSession, source: str
) -> None:
    w = await _world(db_session)
    await assign(db_session, w.holder, w.member)  # still an app member: no `not_member`
    async with make_client(super_admin_org_token_user_rate_limit=100) as client:
        first = await client.post(TOKEN, json=w.body(), headers=w.headers())
        assert first.status_code == 200, first.text
        if source == "designated_assignment_revoked":
            await db_session.delete(w.designated_assignment)
        elif source == "designated_assignment_expired":
            w.designated_assignment.expires_at = PAST
        elif source == "designation_cleared":
            await db_session.execute(
                update(App).where(App.id == w.app.id).values(super_admin_role_id=None)
            )
            db_session.expire(w.app)
        elif source == "admin_super_admin_revoked":
            await db_session.delete(w.admin_assignment)
        else:
            w.admin_assignment.expires_at = PAST
        await db_session.flush()
        second = await client.post(TOKEN, json=w.body(), headers=w.headers())
    assert second.status_code == 403
    assert second.content == DENIED
    assert len(await _rows(db_session, w.org.id)) == 1


async def test_super_admin_mint_writes_one_audit_row(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    async with make_client(access_token_ttl_seconds=43200) as client:
        resp = await client.post(
            TOKEN, json=w.body(), headers={**w.headers(), "User-Agent": "pytest-ua"}
        )
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    (row,) = await _rows(db_session, w.org.id)
    assert row.outcome == "success"
    assert row.actor_user_id == w.holder.id
    assert row.target_type == "organization"
    assert row.target_id == w.org.id
    assert row.app_key == "orgapp"
    assert row.user_agent == "pytest-ua"
    assert row.changes is not None
    assert set(row.changes) == {"jti", "account_session_id", "role_id", "expires_at"}
    assert row.changes["jti"] == claims["jti"]
    assert row.changes["account_session_id"] == str(w.account.id)
    assert row.changes["role_id"] == str(w.designated.id)
    assert datetime.fromisoformat(row.changes["expires_at"]).timestamp() == claims["exp"]


async def test_normal_and_member_mints_write_no_audit_row(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    owner = await make_role(db_session, w.app, "orgapp:org-owner")
    await assign(db_session, w.holder, owner, org_id=w.org.id)
    raw, account = await _ordinary(db_session, w)
    before = await db_session.scalar(select(func.count()).where(AuditLog.action == ACTION))
    async with make_client() as client:
        member = await client.post(TOKEN, json=w.body(), headers=w.headers())
        plain = await client.post(
            TOKEN,
            json={"audience": "orgapp", "account_id": str(account.id)},
            headers=auth_headers("orgapp", cookie=raw),
        )
    assert member.status_code == plain.status_code == 200
    after = await db_session.scalar(select(func.count()).where(AuditLog.action == ACTION))
    assert before == after


async def test_denied_mint_writes_no_audit_row(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(uuid.uuid4()), headers=w.headers())
    assert resp.status_code == 403
    assert await _rows(db_session, w.org.id) == []


async def test_audit_row_has_no_email(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await _world(db_session)
    email = w.holder.email
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
    (row,) = await _rows(db_session, w.org.id)
    dump = str([row.actor_label, row.changes, row.target_type, row.app_key, row.user_agent, row.ip])
    assert email not in dump
    assert resp.json()["access_token"] not in dump


async def test_holder_token_has_no_other_app_role(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    other_app = await make_login_app(db_session, "thirdapp")
    await assign(db_session, w.holder, await make_role(db_session, other_app, "thirdapp:x"))
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert all(r.startswith("orgapp:") for r in claims["roles"])
    assert "admin:" not in str(claims)
    assert "sid" not in claims


async def test_super_admin_mint_rate_limited_429(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    async with make_client(super_admin_org_token_user_rate_limit=1) as client:
        first = await client.post(TOKEN, json=w.body(), headers=w.headers())
        second = await client.post(TOKEN, json=w.body(), headers=w.headers())
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert "retry-after" in second.headers
    assert len(await _rows(db_session, w.org.id)) == 1


async def test_member_mints_do_not_count_toward_super_admin_limit(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    member_org = await _entitled_org(db_session, w.app)
    owner = await make_role(db_session, w.app, "orgapp:org-owner")
    await assign(db_session, w.holder, owner, org_id=member_org.id)
    async with make_client(super_admin_org_token_user_rate_limit=1) as client:
        for _ in range(2):
            resp = await client.post(TOKEN, json=w.body(member_org.id), headers=w.headers())
            assert resp.status_code == 200
        resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
    assert resp.status_code == 200


async def test_non_holder_never_sees_super_admin_429(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    raw, account = await _ordinary(db_session, w)
    body = {"audience": "orgapp", "account_id": str(account.id), "org_id": str(w.org.id)}
    async with make_client(super_admin_org_token_user_rate_limit=1) as client:
        for _ in range(2):
            resp = await client.post(TOKEN, json=body, headers=auth_headers("orgapp", cookie=raw))
            assert resp.status_code == 403


async def test_holder_token_uses_explicit_ttl(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    async with make_client(
        access_token_ttl_seconds=43200, super_admin_org_token_ttl_seconds=600
    ) as client:
        resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert resp.json()["expires_in"] == 600
    assert claims["exp"] - claims["iat"] == 600


async def test_holder_token_ttl_capped_locally(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    async with make_client() as client:
        resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
    assert resp.json()["expires_in"] == 900


async def test_super_admin_mint_logs_no_secrets(
    make_client: MakeClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The migrations' `fileConfig` (session fixture) disables loggers that already exist.
    monkeypatch.setattr(logging.getLogger("app.modules.tokens.service"), "disabled", False)
    w = await _world(db_session)
    email = w.holder.email
    with caplog.at_level(logging.DEBUG):
        async with make_client() as client:
            resp = await client.post(TOKEN, json=w.body(), headers=w.headers())
    lines = [r.getMessage() for r in caplog.records if "token_issued" in r.getMessage()]
    assert len(lines) == 1
    assert "super_admin_org=True" in lines[0]
    assert email not in caplog.text
    assert resp.json()["access_token"] not in caplog.text


async def test_audit_failure_blocks_token(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = await _world(db_session)
    body, headers, org_id = w.body(), w.headers(), w.org.id
    await db_session.commit()

    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(AuditLogger, "record", boom)
    async with make_strict_client() as client:
        with pytest.raises(RuntimeError):
            await client.post(TOKEN, json=body, headers=headers)
    assert await _rows(db_session, org_id) == []
