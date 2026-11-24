import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_token import verify_access_token
from app.core.jwt_keys import build_key_set
from app.modules.entitlements.models import OrgEntitlement
from app.modules.entitlements.schemas import LIMIT_KEY_PATTERN
from app.modules.rbac.models import App, Role
from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.sessions.service import DeviceSessionService
from app.modules.users.models import User
from tests.auth_factories import (
    LOGOUT,
    ORIGIN,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    make_member_role,
    parse_set_cookie,
    sha256,
    shift,
)
from tests.conftest import TEST_JWT_ISSUER, TEST_JWT_SIGNING_KEY, MakeClient
from tests.jwt_verifier import verify_reference
from tests.rbac_factories import assign, make_org, make_role, super_admin_role

TOKEN = "/api/v1/auth/token"
JWKS = "/.well-known/jwks.json"


@pytest.fixture
async def stoktok(db_session: AsyncSession) -> App:
    return await make_login_app(db_session)


@pytest.fixture
async def viewer(db_session: AsyncSession, stoktok: App) -> Role:
    return await make_role(db_session, stoktok, "stoktok:viewer")


def _body(account_id: uuid.UUID, audience: str = "stoktok", **extra: Any) -> dict[str, Any]:
    return {"audience": audience, "account_id": str(account_id), **extra}


async def _member(
    db_session: AsyncSession, role: Role, **user_kwargs: Any
) -> tuple[User, str, AccountSession]:
    """A user holding `role`, signed in on a fresh device."""
    user = await make_login_user(db_session, **user_kwargs)
    await assign(db_session, user, role)
    raw, _, (account,) = await make_device(db_session, user)
    return user, raw, account


def _error(resp: Any) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


async def test_token_happy_path(
    client: AsyncClient, db_session: AsyncSession, stoktok: App, viewer: Role
) -> None:
    editor = await make_role(db_session, stoktok, "stoktok:editor")
    user, raw, account = await _member(db_session, viewer)
    await assign(db_session, user, editor)

    resp = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "audience"}
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    assert body["audience"] == "stoktok"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["pragma"] == "no-cache"
    assert parse_set_cookie(resp) is None

    jwks = (await client.get(JWKS)).json()
    token = body["access_token"]
    header = jwt.get_unverified_header(token)
    assert header == {"alg": "ES256", "kid": jwks["keys"][0]["kid"], "typ": "at+jwt"}
    claims = verify_reference(token, jwks, audience="stoktok", issuer=TEST_JWT_ISSUER)
    assert set(claims) == {
        "iss",
        "aud",
        "sub",
        "email",
        "email_verified",
        "roles",
        "iat",
        "exp",
        "jti",
    }
    assert claims["iss"] == TEST_JWT_ISSUER
    assert claims["aud"] == "stoktok"
    assert claims["sub"] == str(user.id)
    assert claims["email"] == user.email
    assert claims["email_verified"] is True
    assert claims["roles"] == ["stoktok:editor", "stoktok:viewer"]
    assert claims["exp"] - claims["iat"] == 900
    assert uuid.UUID(claims["jti"]).version == 4


async def test_token_ttl_43200(
    make_client: MakeClient, db_session: AsyncSession, viewer: Role
) -> None:
    user, raw, account = await _member(db_session, viewer)
    async with make_client(access_token_ttl_seconds=43200) as client:
        resp = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["expires_in"] == 43200
        jwks = (await client.get(JWKS)).json()
    claims = verify_reference(
        body["access_token"], jwks, audience="stoktok", issuer=TEST_JWT_ISSUER
    )
    assert claims["sub"] == str(user.id)
    assert claims["exp"] - claims["iat"] == 43200


async def test_token_roles_exclude_other_apps(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    other = await make_login_app(db_session, key="ricotdin")
    user, raw, account = await _member(db_session, viewer)
    await assign(db_session, user, await make_role(db_session, other, "ricotdin:viewer"))
    resp = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert claims["roles"] == ["stoktok:viewer"]


async def test_token_two_accounts_one_device_independent(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    await assign(db_session, alice, viewer)
    await assign(db_session, bob, viewer)
    raw, _, (a, b) = await make_device(db_session, alice, bob)
    headers = auth_headers(cookie=raw)

    subs = []
    for account in (a, b):
        resp = await client.post(TOKEN, json=_body(account.id), headers=headers)
        assert resp.status_code == 200, resp.text
        claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
        subs.append(claims["sub"])
    assert subs == [str(alice.id), str(bob.id)]

    out = await client.post(LOGOUT, json={"account_id": str(a.id)}, headers=headers)
    assert out.status_code == 204
    resp_a = await client.post(TOKEN, json=_body(a.id), headers=headers)
    assert resp_a.status_code == 401
    assert _error(resp_a)["code"] == "session_invalid"
    resp_b = await client.post(TOKEN, json=_body(b.id), headers=headers)
    assert resp_b.status_code == 200


async def test_token_unknown_account_401_keeps_cookie(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, _ = await _member(db_session, viewer)
    resp = await client.post(TOKEN, json=_body(uuid.uuid4()), headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert resp.json() == {
        "error": {"code": "session_invalid", "message": "Session is invalid", "details": None}
    }
    assert parse_set_cookie(resp) is None


async def test_token_other_device_account_401(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, _ = await _member(db_session, viewer)
    _, _, foreign = await _member(db_session, viewer)
    resp = await client.post(TOKEN, json=_body(foreign.id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert parse_set_cookie(resp) is None


async def test_token_revoked_account_401(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer)
    await db_session.execute(
        update(AccountSession)
        .where(AccountSession.id == account.id)
        .values(revoked_at=func.now())
        .execution_options(synchronize_session=False)
    )
    resp = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert parse_set_cookie(resp) is None


async def _rotated_with_newer_account(
    db_session: AsyncSession, role: Role
) -> tuple[str, uuid.UUID, uuid.UUID]:
    """Alice (an hour old), then rotation, then bob. Returns the previous raw value."""
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    await assign(db_session, alice, role)
    await assign(db_session, bob, role)
    raw, device, (alice_account,) = await make_device(db_session, alice)
    await shift(db_session, "account_sessions", "created_at", alice_account.id, "-1 hour")
    service = DeviceSessionService(db_session, idle_days=30, max_days=365)
    assert (await service.resolve([raw], force_rotate=True)).new_cookie is not None
    bob_account_id = await service.add_account(device.id, bob.id)  # created_at == rotated_at
    return raw, alice_account.id, bob_account_id


async def test_token_previous_cookie_newer_account_401(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    raw, _, bob_id = await _rotated_with_newer_account(db_session, viewer)
    resp = await client.post(TOKEN, json=_body(bob_id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert parse_set_cookie(resp) is None


async def test_token_previous_cookie_older_account_200(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    raw, alice_id, _ = await _rotated_with_newer_account(db_session, viewer)
    resp = await client.post(TOKEN, json=_body(alice_id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 200, resp.text
    assert parse_set_cookie(resp) is None  # previous value: no re-rotation


async def test_token_disabled_user_401(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer, status="disabled")
    resp = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert parse_set_cookie(resp) is None


async def test_token_email_unverified_403(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer, verified=False)
    resp = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "email_unverified"


@pytest.mark.parametrize(
    ("join_policy", "with_default", "join_available"),
    [
        ("open", True, True),
        ("open", False, False),
        ("approval", True, True),
        ("approval", False, False),
        ("closed", True, False),
    ],
)
async def test_token_not_member(
    client: AsyncClient,
    db_session: AsyncSession,
    join_policy: str,
    with_default: bool,
    join_available: bool,
) -> None:
    app = await make_login_app(db_session, join_policy=join_policy)
    if with_default:
        await make_member_role(db_session, app)
    user = await make_login_user(db_session)
    raw, _, (account,) = await make_device(db_session, user)
    resp = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 403
    assert _error(resp) == {
        "code": "not_member",
        "message": "Not a member of this app",
        "details": {"join_available": join_available},
    }


async def test_token_expired_assignment_not_member(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    user = await make_login_user(db_session)
    await assign(db_session, user, viewer, expires_at=datetime.now(UTC) - timedelta(days=1))
    raw, _, (account,) = await make_device(db_session, user)
    resp = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "not_member"


async def test_token_audience_mismatch(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    other = await make_login_app(db_session, key="ricotdin")
    user, raw, account = await _member(db_session, viewer)
    await assign(db_session, user, await make_role(db_session, other, "ricotdin:viewer"))
    resp = await client.post(
        TOKEN, json=_body(account.id, audience="ricotdin"), headers=auth_headers(cookie=raw)
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "audience_mismatch"


async def _admin_with_origin(db_session: AsyncSession) -> App:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.origins = [ORIGIN]
    await db_session.flush()
    return admin


async def test_token_admin_audience_issued(client: AsyncClient, db_session: AsyncSession) -> None:
    await _admin_with_origin(db_session)
    user, raw, account = await _member(db_session, await super_admin_role(db_session))
    resp = await client.post(
        TOKEN,
        json=_body(account.id, audience="admin"),
        headers=auth_headers(app_key="admin", cookie=raw),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["audience"] == "admin"
    key_set = build_key_set(TEST_JWT_SIGNING_KEY, [])  # the app's signing key
    claims = verify_access_token(
        resp.json()["access_token"], key_set, audience="admin", issuer=TEST_JWT_ISSUER
    )
    assert claims["sub"] == str(user.id)
    assert claims["roles"] == ["admin:super-admin"]
    assert set(claims) == {
        "iss", "aud", "sub", "email", "email_verified", "roles", "iat", "exp", "jti", "sid",
    }  # fmt: skip
    assert claims["sid"] == str(account.id)


async def test_token_admin_not_member(client: AsyncClient, db_session: AsyncSession) -> None:
    await _admin_with_origin(db_session)
    user = await make_login_user(db_session)
    raw, _, (account,) = await make_device(db_session, user)
    resp = await client.post(
        TOKEN,
        json=_body(account.id, audience="admin"),
        headers=auth_headers(app_key="admin", cookie=raw),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "not_member"
    assert _error(resp)["details"] == {"join_available": False}


async def test_token_admin_unverified(client: AsyncClient, db_session: AsyncSession) -> None:
    await _admin_with_origin(db_session)
    _, raw, account = await _member(db_session, await super_admin_role(db_session), verified=False)
    resp = await client.post(
        TOKEN,
        json=_body(account.id, audience="admin"),
        headers=auth_headers(app_key="admin", cookie=raw),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "email_unverified"


async def _calechip(db_session: AsyncSession) -> tuple[App, Role]:
    """An `org_mode = required` app with a `calechip:member` role."""
    app = await make_login_app(db_session, "calechip", org_mode="required")
    return app, await make_role(db_session, app, "calechip:member")


def _cal_headers(raw: str) -> dict[str, str]:
    return auth_headers(app_key="calechip", cookie=raw)


async def test_token_with_org_has_org_claim_and_union_roles(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, member = await _calechip(db_session)
    owner = await make_role(db_session, app, "calechip:org-owner")
    org = await make_org(db_session)
    other = await make_org(db_session)
    user, raw, account = await _member(db_session, member)
    await assign(db_session, user, owner, org_id=org.id)
    await assign(db_session, user, await make_role(db_session, app, "calechip:x"), org_id=other.id)

    resp = await client.post(
        TOKEN,
        json=_body(account.id, "calechip", org_id=str(org.id)),
        headers=_cal_headers(raw),
    )
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert claims["org"] == str(org.id)
    assert claims["roles"] == ["calechip:member", "calechip:org-owner"]  # other org's role absent
    assert claims["org_roles"] == ["calechip:org-owner"]


async def _org_claims(client: AsyncClient, raw: str, account_id: Any, org: Any) -> dict[str, Any]:
    resp = await client.post(
        TOKEN,
        json=_body(account_id, "calechip", org_id=str(org.id)),
        headers=_cal_headers(raw),
    )
    assert resp.status_code == 200, resp.text
    claims: dict[str, Any] = jwt.decode(
        resp.json()["access_token"], options={"verify_signature": False}
    )
    return claims


async def test_org_roles_excludes_app_scope_only_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, member = await _calechip(db_session)
    editor = await make_role(db_session, app, "calechip:editor")
    owner = await make_role(db_session, app, "calechip:org-owner")
    org = await make_org(db_session)
    user, raw, account = await _member(db_session, member)
    await assign(db_session, user, editor)  # staff-granted, app scope
    await assign(db_session, user, owner, org_id=org.id)
    claims = await _org_claims(client, raw, account.id, org)
    assert claims["roles"] == ["calechip:editor", "calechip:member", "calechip:org-owner"]
    assert claims["org_roles"] == ["calechip:org-owner"]


async def test_org_roles_role_in_both_scopes_in_both_claims(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, member = await _calechip(db_session)
    org = await make_org(db_session)
    user, raw, account = await _member(db_session, member)
    await assign(db_session, user, member, org_id=org.id)
    claims = await _org_claims(client, raw, account.id, org)
    assert claims["roles"] == ["calechip:member"]
    assert claims["org_roles"] == ["calechip:member"]


async def test_org_roles_excludes_other_org_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, member = await _calechip(db_session)
    owner = await make_role(db_session, app, "calechip:org-owner")
    viewer = await make_role(db_session, app, "calechip:org-viewer")
    org_a = await make_org(db_session)
    org_b = await make_org(db_session)
    user, raw, account = await _member(db_session, member)
    await assign(db_session, user, viewer, org_id=org_a.id)
    await assign(db_session, user, owner, org_id=org_b.id)
    claims = await _org_claims(client, raw, account.id, org_a)
    assert claims["org_roles"] == ["calechip:org-viewer"]


async def test_token_without_org_app_scope_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, member = await _calechip(db_session)
    org = await make_org(db_session)
    user, raw, account = await _member(db_session, member)
    await assign(db_session, user, await make_role(db_session, app, "calechip:x"), org_id=org.id)

    resp = await client.post(TOKEN, json=_body(account.id, "calechip"), headers=_cal_headers(raw))
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert "org" not in claims and "org_roles" not in claims
    assert "ent" not in claims
    assert claims["roles"] == ["calechip:member"]


async def test_token_app_member_without_org_role_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The F2 regression: an app-scope role must not make any org usable."""
    _, member = await _calechip(db_session)
    org = await make_org(db_session)
    _, raw, account = await _member(db_session, member)
    resp = await client.post(
        TOKEN,
        json=_body(account.id, "calechip", org_id=str(org.id)),
        headers=_cal_headers(raw),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_access_denied"


async def test_token_unknown_org_403_same_as_non_member(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, member = await _calechip(db_session)
    org = await make_org(db_session)
    _, raw, account = await _member(db_session, member)
    answers = []
    for org_id in (uuid.uuid4(), org.id):
        resp = await client.post(
            TOKEN,
            json=_body(account.id, "calechip", org_id=str(org_id)),
            headers=_cal_headers(raw),
        )
        answers.append((resp.status_code, resp.json()))
    assert answers[0] == answers[1]
    assert answers[0][0] == 403


async def test_token_suspended_org_403(client: AsyncClient, db_session: AsyncSession) -> None:
    app, member = await _calechip(db_session)
    owner = await make_role(db_session, app, "calechip:org-owner")
    org = await make_org(db_session, status="suspended")
    user, raw, account = await _member(db_session, member)
    await assign(db_session, user, owner, org_id=org.id)
    resp = await client.post(
        TOKEN,
        json=_body(account.id, "calechip", org_id=str(org.id)),
        headers=_cal_headers(raw),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_access_denied"


async def test_token_expired_org_role_403(client: AsyncClient, db_session: AsyncSession) -> None:
    app, member = await _calechip(db_session)
    owner = await make_role(db_session, app, "calechip:org-owner")
    org = await make_org(db_session)
    user, raw, account = await _member(db_session, member)
    past = datetime(2000, 1, 1, tzinfo=UTC)
    await assign(db_session, user, owner, org_id=org.id, expires_at=past)
    resp = await client.post(
        TOKEN,
        json=_body(account.id, "calechip", org_id=str(org.id)),
        headers=_cal_headers(raw),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_access_denied"


async def test_token_other_app_role_in_org_does_not_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, member = await _calechip(db_session)
    stoktok = await make_login_app(db_session)
    foreign = await make_role(db_session, stoktok, "stoktok:viewer")
    org = await make_org(db_session)
    user, raw, account = await _member(db_session, member)
    await assign(db_session, user, foreign, org_id=org.id)
    resp = await client.post(
        TOKEN,
        json=_body(account.id, "calechip", org_id=str(org.id)),
        headers=_cal_headers(raw),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_access_denied"


async def test_token_org_less_app_ignores_org_id_422(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer)
    resp = await client.post(
        TOKEN, json=_body(account.id, org_id=str(uuid.uuid4())), headers=auth_headers(cookie=raw)
    )
    assert resp.status_code == 422
    assert _error(resp)["code"] == "org_not_supported"
    assert _error(resp)["details"] == [{"field": "org_id", "reason": "not_supported"}]


async def test_token_admin_audience_org_id_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _admin_with_origin(db_session)
    _, raw, account = await _member(db_session, await super_admin_role(db_session))
    resp = await client.post(
        TOKEN,
        json=_body(account.id, audience="admin", org_id=str(uuid.uuid4())),
        headers=auth_headers(app_key="admin", cookie=raw),
    )
    assert resp.status_code == 422
    assert _error(resp)["code"] == "org_not_supported"


@pytest.mark.parametrize(
    "body",
    [
        {"audience": "stoktok", "account_id": str(uuid.uuid4()), "extra": 1},
        {"audience": "Stoktok!", "account_id": str(uuid.uuid4())},
        {"audience": "stoktok", "account_id": "not-a-uuid"},
        {"audience": "stoktok"},
        {"account_id": str(uuid.uuid4())},
        {"audience": "stoktok", "account_id": str(uuid.uuid4()), "ent": {"plan": "x"}},
    ],
)
async def test_token_body_validation_422(
    client: AsyncClient, db_session: AsyncSession, viewer: Role, body: dict[str, Any]
) -> None:
    _, raw, _ = await _member(db_session, viewer)
    resp = await client.post(TOKEN, json=body, headers=auth_headers(cookie=raw))
    assert resp.status_code == 422
    assert _error(resp)["code"] == "validation_error"


async def test_token_missing_cookie_401(client: AsyncClient, stoktok: App) -> None:
    resp = await client.post(TOKEN, json=_body(uuid.uuid4()), headers=auth_headers())
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"


async def test_token_missing_app_header_403(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer)
    resp = await client.post(
        TOKEN, json=_body(account.id), headers=auth_headers(app_key=None, cookie=raw)
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "app_header_required"


async def test_token_bad_origin_403(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer)
    for origin in ("https://evil.example", None):
        resp = await client.post(
            TOKEN, json=_body(account.id), headers=auth_headers(origin=origin, cookie=raw)
        )
        assert resp.status_code == 403
        assert _error(resp)["code"] == "origin_not_allowed"


async def test_token_unknown_app_403(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer)
    resp = await client.post(
        TOKEN,
        json=_body(account.id, audience="nope-app"),
        headers=auth_headers(app_key="nope-app", cookie=raw),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "origin_not_allowed"


async def test_token_disabled_app_403(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    disabled = await make_login_app(db_session, key="ricotdin", status="disabled")
    user, raw, account = await _member(db_session, viewer)
    await assign(db_session, user, await make_role(db_session, disabled, "ricotdin:viewer"))
    resp = await client.post(
        TOKEN,
        json=_body(account.id, audience="ricotdin"),
        headers=auth_headers(app_key="ricotdin", cookie=raw),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "origin_not_allowed"


async def _token_hash(db_session: AsyncSession, device_id: uuid.UUID) -> bytes:
    value = await db_session.scalar(
        select(DeviceSession.token_hash)
        .where(DeviceSession.id == device_id)
        .execution_options(populate_existing=True)
    )
    assert value is not None
    return value


async def test_token_rotation_forwards_set_cookie(
    make_strict_client: MakeClient, db_session: AsyncSession, viewer: Role
) -> None:
    user = await make_login_user(db_session)
    await assign(db_session, user, viewer)
    raw, device, (account,) = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    device_id, account_id = device.id, account.id
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(TOKEN, json=_body(account_id), headers=auth_headers(cookie=raw))
        assert resp.status_code == 200, resp.text
        morsel = parse_set_cookie(resp)
        assert morsel is not None and morsel.value
        assert morsel["path"] == "/"
        assert await _token_hash(db_session, device_id) == sha256(morsel.value)
        again = await client.post(
            TOKEN, json=_body(account_id), headers=auth_headers(cookie=morsel.value)
        )
        assert again.status_code == 200, again.text
        assert parse_set_cookie(again) is None


async def test_token_error_after_rotation_rolls_back(
    make_strict_client: MakeClient, db_session: AsyncSession, stoktok: App
) -> None:
    user = await make_login_user(db_session)  # no role: not_member
    raw, device, (account,) = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    device_id, account_id = device.id, account.id
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(TOKEN, json=_body(account_id), headers=auth_headers(cookie=raw))
        assert resp.status_code == 403
        assert _error(resp)["code"] == "not_member"
        assert parse_set_cookie(resp) is None
        assert await _token_hash(db_session, device_id) == sha256(raw)
        again = await client.post(TOKEN, json=_body(account_id), headers=auth_headers(cookie=raw))
        assert again.status_code == 403  # the old cookie still resolves
        assert _error(again)["code"] == "not_member"


async def test_token_rate_limited_429(
    make_client: MakeClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer)
    async with make_client(token_rate_limit=1) as client:
        first = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
        assert first.status_code == 200
        second = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
    assert second.status_code == 429
    assert _error(second)["code"] == "rate_limited"
    assert "retry-after" in second.headers


async def test_token_logs_no_secrets(
    client: AsyncClient,
    db_session: AsyncSession,
    viewer: Role,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The migrations' `fileConfig` (session fixture) disables loggers that already exist.
    for name in ("app.modules.tokens.service", "app.modules.sessions.service"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    user, raw, account = await _member(db_session, viewer)
    with caplog.at_level(logging.DEBUG):
        resp = await client.post(TOKEN, json=_body(account.id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    kid = jwt.get_unverified_header(token)["kid"]
    text = caplog.text
    assert token not in text
    assert token.split(".")[2] not in text
    assert raw not in text
    assert user.email not in text
    issued = [r.getMessage() for r in caplog.records if "token_issued" in r.getMessage()]
    assert len(issued) == 1
    assert claims["jti"] in issued[0]
    assert kid in issued[0]


async def _entitled_org(
    db_session: AsyncSession, **ent_kwargs: Any
) -> tuple[App, Any, str, AccountSession]:
    """calechip, an org the member holds a role in, and an entitlement row for it."""
    app, member = await _calechip(db_session)
    owner = await make_role(db_session, app, "calechip:org-owner")
    org = await make_org(db_session)
    user, raw, account = await _member(db_session, member)
    await assign(db_session, user, owner, org_id=org.id)
    fields: dict[str, Any] = {"plan": "pro", "limits": {}, **ent_kwargs}
    db_session.add(OrgEntitlement(org_id=org.id, app_id=app.id, **fields))
    await db_session.flush()
    return app, org, raw, account


async def test_token_org_entitled_has_ent(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org, raw, account = await _entitled_org(
        db_session, plan="pro", limits={"seats": 5, "projects": 3}
    )
    resp = await client.post(
        TOKEN, json=_body(account.id, "calechip", org_id=str(org.id)), headers=_cal_headers(raw)
    )
    assert resp.status_code == 200, resp.text
    jwks = (await client.get(JWKS)).json()
    claims = verify_reference(
        resp.json()["access_token"], jwks, audience="calechip", issuer=TEST_JWT_ISSUER
    )
    assert claims["ent"] == {"plan": "pro", "limits": {"seats": 5, "projects": 3}}
    assert set(claims) == {
        "iss", "aud", "sub", "email", "email_verified", "roles", "org", "org_roles", "ent",
        "iat", "exp", "jti",
    }  # fmt: skip


async def test_token_org_entitled_empty_limits(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, org, raw, account = await _entitled_org(db_session, plan="pro", limits={})
    claims = await _org_claims(client, raw, account.id, org)
    assert claims["ent"] == {"plan": "pro", "limits": {}}


@pytest.mark.parametrize("case", ["none", "disabled", "other_app_only"])
async def test_token_org_not_entitled_ent_null(
    client: AsyncClient, db_session: AsyncSession, case: str
) -> None:
    _, org, raw, account = await _entitled_org(db_session, enabled=case != "disabled")
    if case == "none":
        await db_session.execute(
            OrgEntitlement.__table__.delete().where(OrgEntitlement.org_id == org.id)
        )
    if case == "other_app_only":
        await db_session.execute(
            OrgEntitlement.__table__.delete().where(OrgEntitlement.org_id == org.id)
        )
        other = await make_login_app(db_session, "secretapp", org_mode="required")
        db_session.add(OrgEntitlement(org_id=org.id, app_id=other.id, plan="secret", limits={}))
        await db_session.flush()
    resp = await client.post(
        TOKEN, json=_body(account.id, "calechip", org_id=str(org.id)), headers=_cal_headers(raw)
    )
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert "ent" in claims and claims["ent"] is None
    assert "secret" not in str(claims)


async def test_token_without_org_has_no_ent(client: AsyncClient, db_session: AsyncSession) -> None:
    _, _, raw, account = await _entitled_org(db_session)
    resp = await client.post(TOKEN, json=_body(account.id, "calechip"), headers=_cal_headers(raw))
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert "ent" not in claims


async def test_token_ent_reflects_update_on_next_mint(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, org, raw, account = await _entitled_org(db_session, plan="pro", limits={"seats": 5})
    assert (await _org_claims(client, raw, account.id, org))["ent"] == {
        "plan": "pro",
        "limits": {"seats": 5},
    }
    await db_session.execute(
        update(OrgEntitlement)
        .where(OrgEntitlement.org_id == org.id)
        .values(plan="basic", limits={"seats": 1})
    )
    assert (await _org_claims(client, raw, account.id, org))["ent"] == {
        "plan": "basic",
        "limits": {"seats": 1},
    }
    await db_session.execute(
        update(OrgEntitlement).where(OrgEntitlement.org_id == org.id).values(enabled=False)
    )
    assert (await _org_claims(client, raw, account.id, org))["ent"] is None


async def test_token_worst_case_ent_size(client: AsyncClient, db_session: AsyncSession) -> None:
    limits = {("k" + str(i).zfill(2)).ljust(64, "x"): 2**53 - 1 for i in range(32)}
    assert all(re.fullmatch(LIMIT_KEY_PATTERN, k) for k in limits)
    plan = "p" * 63
    _, org, raw, account = await _entitled_org(db_session, plan=plan, limits=limits)
    resp = await client.post(
        TOKEN, json=_body(account.id, "calechip", org_id=str(org.id)), headers=_cal_headers(raw)
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["ent"] == {"plan": plan, "limits": limits}
    assert len(token) < 8192


async def test_token_org_body_unchanged(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org, raw, account = await _entitled_org(db_session)
    resp = await client.post(
        TOKEN, json=_body(account.id, "calechip", org_id=str(org.id)), headers=_cal_headers(raw)
    )
    assert set(resp.json()) == {"access_token", "token_type", "expires_in", "audience"}


async def test_token_logs_no_entitlement_values(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("app.modules.tokens.service", "app.modules.sessions.service"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    _, org, raw, account = await _entitled_org(
        db_session, plan="zzplanmarker", limits={"zzlimitmarker": 7}
    )
    with caplog.at_level(logging.DEBUG):
        resp = await client.post(
            TOKEN,
            json=_body(account.id, "calechip", org_id=str(org.id)),
            headers=_cal_headers(raw),
        )
    assert resp.status_code == 200
    assert "zzplanmarker" not in caplog.text
    assert "zzlimitmarker" not in caplog.text
    assert len([r for r in caplog.records if "token_issued" in r.getMessage()]) == 1


_NAME_CLAIMS = {
    "iss", "aud", "sub", "email", "email_verified", "roles", "iat", "exp", "jti", "name",
}  # fmt: skip


async def _mint_stoktok(client: AsyncClient, raw: str, account_id: uuid.UUID) -> str:
    resp = await client.post(TOKEN, json=_body(account_id), headers=auth_headers(cookie=raw))
    assert resp.status_code == 200, resp.text
    token: str = resp.json()["access_token"]
    return token


async def test_token_name_claim_present(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    user, raw, account = await _member(db_session, viewer)
    user.display_name = "Nguyễn Văn A"
    await db_session.flush()
    jwks = (await client.get(JWKS)).json()
    claims = verify_reference(
        await _mint_stoktok(client, raw, account.id),
        jwks,
        audience="stoktok",
        issuer=TEST_JWT_ISSUER,
    )
    assert claims["name"] == "Nguyễn Văn A"
    assert set(claims) == _NAME_CLAIMS


async def test_token_name_claim_absent_when_unset(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer)
    token = await _mint_stoktok(client, raw, account.id)
    assert "name" not in jwt.decode(token, options={"verify_signature": False})


@pytest.mark.parametrize("value", ["", "  "])
async def test_token_name_claim_absent_when_empty_or_blank(
    client: AsyncClient, db_session: AsyncSession, viewer: Role, value: str
) -> None:
    user, raw, account = await _member(db_session, viewer)
    user.display_name = value
    await db_session.flush()
    token = await _mint_stoktok(client, raw, account.id)
    assert "name" not in jwt.decode(token, options={"verify_signature": False})


async def test_token_admin_has_no_name_claim(client: AsyncClient, db_session: AsyncSession) -> None:
    await _admin_with_origin(db_session)
    user, raw, account = await _member(db_session, await super_admin_role(db_session))
    user.display_name = "Staff Person"
    await db_session.flush()
    resp = await client.post(
        TOKEN,
        json=_body(account.id, audience="admin"),
        headers=auth_headers(app_key="admin", cookie=raw),
    )
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert set(claims) == {
        "iss", "aud", "sub", "email", "email_verified", "roles", "iat", "exp", "jti", "sid",
    }  # fmt: skip


async def test_token_name_claim_with_org(client: AsyncClient, db_session: AsyncSession) -> None:
    _, org, raw, account = await _entitled_org(db_session, plan="pro", limits={})
    user = await db_session.scalar(select(User).where(User.id == account.user_id))
    assert user is not None
    user.display_name = "Org Person"
    await db_session.flush()
    resp = await client.post(
        TOKEN, json=_body(account.id, "calechip", org_id=str(org.id)), headers=_cal_headers(raw)
    )
    assert resp.status_code == 200, resp.text
    jwks = (await client.get(JWKS)).json()
    claims = verify_reference(
        resp.json()["access_token"], jwks, audience="calechip", issuer=TEST_JWT_ISSUER
    )
    assert claims["name"] == "Org Person"
    assert set(claims) == {
        "iss", "aud", "sub", "email", "email_verified", "roles", "org", "org_roles", "ent",
        "name", "iat", "exp", "jti",
    }  # fmt: skip


@pytest.mark.parametrize("bad", ["Ada" + chr(c) for c in (0x202E, 0x200B, 0x0007)])
async def test_token_name_out_of_contract_omitted(
    client: AsyncClient, db_session: AsyncSession, viewer: Role, bad: str
) -> None:
    user, raw, account = await _member(db_session, viewer)
    user.display_name = bad
    await db_session.flush()
    token = await _mint_stoktok(client, raw, account.id)
    assert "name" not in jwt.decode(token, options={"verify_signature": False})


async def test_token_name_reflects_update_on_next_mint(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    user, raw, account = await _member(db_session, viewer)
    user.display_name = "Old Name"
    await db_session.flush()
    first = await _mint_stoktok(client, raw, account.id)
    user.display_name = "New Name"
    await db_session.flush()
    second = await _mint_stoktok(client, raw, account.id)
    assert jwt.decode(first, options={"verify_signature": False})["name"] == "Old Name"
    assert jwt.decode(second, options={"verify_signature": False})["name"] == "New Name"


async def test_token_logs_no_name(
    client: AsyncClient,
    db_session: AsyncSession,
    viewer: Role,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("app.modules.tokens.service", "app.modules.sessions.service"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    user, raw, account = await _member(db_session, viewer)
    user.display_name = "zznamemarker"
    await db_session.flush()
    with caplog.at_level(logging.DEBUG):
        token = await _mint_stoktok(client, raw, account.id)
    assert jwt.decode(token, options={"verify_signature": False})["name"] == "zznamemarker"
    assert "zznamemarker" not in caplog.text
    assert len([r for r in caplog.records if "token_issued" in r.getMessage()]) == 1

    # Out-of-contract name: one warning with user_id, never the value.
    user.display_name = "zznamemarker" + chr(0x202E)
    await db_session.flush()
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        token = await _mint_stoktok(client, raw, account.id)
    assert "name" not in jwt.decode(token, options={"verify_signature": False})
    rejected = [r.getMessage() for r in caplog.records if "name_out_of_contract" in r.getMessage()]
    assert len(rejected) == 1
    assert str(user.id) in rejected[0]
    assert "zznamemarker" not in caplog.text
