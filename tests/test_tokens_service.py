"""`TokenService` guards that the HTTP tests cannot reach (ADM-018)."""

import logging

import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jwt_keys import build_key_set
from app.core.rate_limit import InMemoryRateLimiter, RateLimit
from app.modules.audit.context import AuditContext
from app.modules.entitlements.models import OrgEntitlement
from app.modules.rbac.models import App
from app.modules.sessions.service import ResolvedDevice
from app.modules.tokens.schemas import TokenRequest
from app.modules.tokens.service import TokenService
from tests.auth_factories import make_device, make_login_app, make_login_user
from tests.conftest import TEST_JWT_ISSUER, TEST_JWT_SIGNING_KEY
from tests.rbac_factories import assign, make_org, make_role, super_admin_role


def _service(db: AsyncSession) -> TokenService:
    return TokenService(
        db,
        key_set=build_key_set(TEST_JWT_SIGNING_KEY, []),
        issuer=TEST_JWT_ISSUER,
        ttl_seconds=900,
        super_admin_ttl_seconds=900,
        limiter=InMemoryRateLimiter(),
        super_admin_limit=RateLimit(30, 3600),
        join_reject_cooldown_seconds=0,
    )


async def test_issue_admin_never_has_ent(db_session: AsyncSession) -> None:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.org_mode = "required"
    org = await make_org(db_session)
    user = await make_login_user(db_session)
    role = await super_admin_role(db_session)
    await assign(db_session, user, role)
    await assign(db_session, user, role, org_id=org.id)
    db_session.add(OrgEntitlement(org_id=org.id, app_id=admin.id, plan="pro", limits={}))
    _, device, (account,) = await make_device(db_session, user)
    resolved = ResolvedDevice(device_id=device.id, new_cookie=None, accounts_created_before=None)

    issued = await _service(db_session).issue(
        admin,
        resolved,
        TokenRequest(audience="admin", account_id=account.id, org_id=org.id),
        ctx=AuditContext(),
    )
    claims = jwt.decode(issued.access_token, options={"verify_signature": False})
    assert "org" in claims and "sid" in claims
    assert "ent" not in claims


async def test_ent_out_of_contract_falls_back_to_null(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging.getLogger("app.modules.tokens.service"), "disabled", False)
    app = await make_login_app(db_session, "calechip", org_mode="required")
    member = await make_role(db_session, app, "calechip:member")
    org = await make_org(db_session)
    user = await make_login_user(db_session)
    await assign(db_session, user, member)
    await assign(db_session, user, member, org_id=org.id)
    limits = {f"zzkey{i}": 1 for i in range(33)}
    ent = OrgEntitlement(org_id=org.id, app_id=app.id, plan="pro", limits=limits)
    db_session.add(ent)
    _, device, (account,) = await make_device(db_session, user)
    resolved = ResolvedDevice(device_id=device.id, new_cookie=None, accounts_created_before=None)

    with caplog.at_level(logging.WARNING):
        issued = await _service(db_session).issue(
            app,
            resolved,
            TokenRequest(audience="calechip", account_id=account.id, org_id=org.id),
            ctx=AuditContext(),
        )
    claims = jwt.decode(issued.access_token, options={"verify_signature": False})
    assert "ent" in claims and claims["ent"] is None
    lines = [r.getMessage() for r in caplog.records if "ent_out_of_contract" in r.getMessage()]
    assert len(lines) == 1
    assert str(ent.id) in lines[0]
    assert "zzkey" not in caplog.text
