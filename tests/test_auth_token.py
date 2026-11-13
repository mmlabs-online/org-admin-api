import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
from tests.conftest import TEST_JWT_ISSUER, MakeClient
from tests.jwt_verifier import verify_reference
from tests.rbac_factories import assign, make_role, super_admin_role

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
        ("approval", True, False),  # USR-03g makes approval joinable again
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


async def test_token_admin_audience_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.origins = [ORIGIN]
    await db_session.flush()
    _, raw, account = await _member(db_session, await super_admin_role(db_session))
    resp = await client.post(
        TOKEN,
        json=_body(account.id, audience="admin"),
        headers=auth_headers(app_key="admin", cookie=raw),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "audience_not_supported"


async def test_token_org_id_422(
    client: AsyncClient, db_session: AsyncSession, viewer: Role
) -> None:
    _, raw, account = await _member(db_session, viewer)
    resp = await client.post(
        TOKEN, json=_body(account.id, org_id=str(uuid.uuid4())), headers=auth_headers(cookie=raw)
    )
    assert resp.status_code == 422
    assert _error(resp)["code"] == "org_not_supported"
    assert _error(resp)["details"] == [{"field": "org_id", "reason": "not_supported"}]


@pytest.mark.parametrize(
    "body",
    [
        {"audience": "stoktok", "account_id": str(uuid.uuid4()), "extra": 1},
        {"audience": "Stoktok!", "account_id": str(uuid.uuid4())},
        {"audience": "stoktok", "account_id": "not-a-uuid"},
        {"audience": "stoktok"},
        {"account_id": str(uuid.uuid4())},
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
