"""Admin Bearer auth on real tokens, no `current_admin` override (ADM-002, ADR-0008)."""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from httpx import AsyncClient, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, RoleAssignment
from app.modules.users.models import User
from tests.admin_auth_factories import (
    KEY_SET,
    bearer,
    claims_for,
    make_staff,
    mint,
    mint_raw,
)
from tests.auth_factories import (
    ORIGIN,
    auth_headers,
    login_body,
    make_device,
    make_login_user,
    parse_set_cookie,
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

ORGS = "/api/v1/organizations"
UNAUTHORIZED = {"code": "unauthorized", "message": "Authentication required", "details": None}
CHALLENGE = 'Bearer realm="admin"'
INVALID_CHALLENGE = 'Bearer realm="admin", error="invalid_token"'


@pytest.fixture
async def flags_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client() as c:
        yield c


def _assert_401(resp: Response, challenge: str = INVALID_CHALLENGE) -> None:
    assert resp.status_code == 401, resp.text
    assert resp.json() == {"error": UNAUTHORIZED}
    assert resp.headers["www-authenticate"] == challenge


# --- 401 ---------------------------------------------------------------------


async def test_no_token_401(client: AsyncClient) -> None:
    _assert_401(await client.get(ORGS), CHALLENGE)


async def test_basic_scheme_401(client: AsyncClient) -> None:
    _assert_401(await client.get(ORGS, headers={"Authorization": "Basic dXNlcjpwYXNz"}), CHALLENGE)


async def test_empty_bearer_401(client: AsyncClient) -> None:
    _assert_401(await client.get(ORGS, headers={"Authorization": "Bearer "}), CHALLENGE)


async def test_query_token_401(client: AsyncClient, db_session: AsyncSession) -> None:
    token = mint(await make_staff(db_session, "admin:*"))
    _assert_401(await client.get(ORGS, params={"access_token": token}), CHALLENGE)


def _public_pem() -> bytes:
    return KEY_SET.public_keys[KEY_SET.signing_kid].public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


_HEADERS = {"kid": KEY_SET.signing_kid, "typ": "at+jwt"}
REJECTED: dict[str, Callable[[User], str]] = {
    "expired": lambda u: mint(u, iat=int(time.time()) - 1000, exp=int(time.time()) - 60),
    "wrong_aud": lambda u: mint(u, aud="stoktok"),
    "list_aud": lambda u: mint(u, aud=["admin"]),
    "list_aud_multi": lambda u: mint(u, aud=["stoktok", "admin"]),
    "wrong_iss": lambda u: mint(u, iss="https://evil.invalid"),
    "unknown_kid": lambda u: mint_raw(
        {"kid": "nope", "typ": "at+jwt"}, claims_for(u), KEY_SET.signing_key, "ES256"
    ),
    "alg_none": lambda u: mint_raw(_HEADERS, claims_for(u), None, "none"),
    "hs256": lambda u: mint_raw(_HEADERS, claims_for(u), _public_pem(), "HS256"),
    "typ_jwt": lambda u: mint_raw(
        {"kid": KEY_SET.signing_kid, "typ": "JWT"}, claims_for(u), KEY_SET.signing_key, "ES256"
    ),
    "not_a_jwt": lambda _: "not-a-jwt",
}


@pytest.mark.parametrize("case", list(REJECTED))
async def test_rejected_tokens_401(
    client: AsyncClient, db_session: AsyncSession, case: str
) -> None:
    staff = await make_staff(db_session, "admin:*")
    _assert_401(await client.get(ORGS, headers=bearer(REJECTED[case](staff))))


async def test_unknown_sub_401(client: AsyncClient) -> None:
    _assert_401(await client.get(ORGS, headers=bearer(mint(str(uuid.uuid4())))))


async def test_non_uuid_sub_401(client: AsyncClient) -> None:
    _assert_401(await client.get(ORGS, headers=bearer(mint("not-a-uuid"))))


async def test_disabled_user_401(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:*", status="disabled")
    _assert_401(await client.get(ORGS, headers=bearer(mint(staff))))


async def test_unverified_user_401(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:*", verified=False)
    _assert_401(await client.get(ORGS, headers=bearer(mint(staff, email_verified=True))))


async def test_cookie_only_401(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:*")
    raw, _, _ = await make_device(db_session, staff)
    _assert_401(await client.get(ORGS, headers=auth_headers(cookie=raw)), CHALLENGE)


async def test_auth_runs_before_validation(client: AsyncClient) -> None:
    _assert_401(await client.post(ORGS, json={}), CHALLENGE)
    _assert_401(await client.get(f"{ORGS}/not-a-uuid"), CHALLENGE)


# --- 403 / permissions -------------------------------------------------------


async def test_missing_permission_403(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session)
    resp = await client.get(ORGS, headers=bearer(mint(staff)))
    assert resp.status_code == 403
    assert resp.json() == {
        "error": {
            "code": "permission_denied",
            "message": "Missing permission",
            "details": {"required": "admin:organizations:read"},
        }
    }


async def test_leaf_permission_allows_only_its_routes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = bearer(mint(await make_staff(db_session, "admin:organizations:read")))
    assert (await client.get(ORGS, headers=headers)).status_code == 200
    resp = await client.post(ORGS, json={"slug": "acme", "name": "Acme"}, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["details"] == {"required": "admin:organizations:write"}


async def test_super_admin_wildcard_allows_all(
    flags_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    await assign(db_session, user, await super_admin_role(db_session))
    headers = bearer(mint(user))
    assert (await flags_client.get(ORGS, headers=headers)).status_code == 200
    created = await flags_client.post(ORGS, json={"slug": "acme", "name": "Acme"}, headers=headers)
    assert created.status_code == 201
    assert (await flags_client.get("/api/v1/users", headers=headers)).status_code == 200
    assert (await flags_client.get("/api/v1/apps", headers=headers)).status_code == 200


async def test_other_app_wildcard_does_not_grant_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:owner")
    await grant(
        db_session, role, await make_permission(db_session, app, "stoktok:*", is_admin=True)
    )
    user = await make_login_user(db_session)
    await assign(db_session, user, role)
    resp = await client.get(ORGS, headers=bearer(mint(user)))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"


async def test_role_revoked_after_issue_403(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:organizations:read")
    headers = bearer(mint(staff))
    assert (await client.get(ORGS, headers=headers)).status_code == 200
    await db_session.execute(delete(RoleAssignment).where(RoleAssignment.user_id == staff.id))
    await db_session.flush()
    assert (await client.get(ORGS, headers=headers)).status_code == 403


async def test_expired_assignment_403(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(
        db_session, "admin:*", expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    assert (await client.get(ORGS, headers=bearer(mint(staff)))).status_code == 403


async def test_disabled_after_issue_401(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:*")
    headers = bearer(mint(staff))
    assert (await client.get(ORGS, headers=headers)).status_code == 200
    staff.status = "disabled"
    await db_session.flush()
    _assert_401(await client.get(ORGS, headers=headers))


# --- end to end and logging --------------------------------------------------


async def test_issued_admin_token_end_to_end(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.origins = [ORIGIN]
    await db_session.flush()
    user = await make_login_user(db_session)
    await assign(db_session, user, await super_admin_role(db_session))

    login = await client.post(
        "/api/v1/auth/login", json=login_body(user), headers=auth_headers(app_key="admin")
    )
    assert login.status_code == 200, login.text
    cookie = parse_set_cookie(login)
    assert cookie is not None
    token_resp = await client.post(
        "/api/v1/auth/token",
        json={"audience": "admin", "account_id": login.json()["account_id"]},
        headers=auth_headers(app_key="admin", cookie=cookie.value),
    )
    assert token_resp.status_code == 200, token_resp.text
    resp = await client.get(ORGS, headers=bearer(token_resp.json()["access_token"]))
    assert resp.status_code == 200, resp.text


async def test_denials_logged_without_secrets(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The migrations' `fileConfig` (session fixture) disables loggers that already exist.
    monkeypatch.setattr(logging.getLogger("app.modules.admin_auth.deps"), "disabled", False)
    staff = await make_staff(db_session)
    bad = mint(staff, aud="stoktok")
    good_claims = claims_for(staff)
    good = KEY_SET.sign(good_claims)
    with caplog.at_level(logging.INFO, logger="app.modules.admin_auth.deps"):
        assert (await client.get(ORGS, headers=bearer(bad))).status_code == 401
        assert (await client.get(ORGS, headers=bearer(good))).status_code == 403
    messages = [r.getMessage() for r in caplog.records]
    denied = [m for m in messages if m.startswith("admin_auth_denied")]
    assert denied == ["admin_auth_denied reason=audience jti=- user_id=-"]
    forbidden = [m for m in messages if m.startswith("admin_permission_denied")]
    assert forbidden == [
        f"admin_permission_denied user_id={staff.id} jti={good_claims['jti']} "
        "required=admin:organizations:read"
    ]
    for token in (bad, good):
        assert token not in caplog.text
        assert token.split(".")[2] not in caplog.text
    assert staff.email not in caplog.text
