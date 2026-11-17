"""Admin tokens end with their account session (USR-14, ADR-0008 amendment): real `/auth/token`."""

import secrets
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_token import verify_access_token
from app.modules.auth.password_reset import RESET_TTL
from app.modules.auth.repository import EmailTokenRepository
from app.modules.rbac.models import App
from app.modules.sessions.repository import SessionRepository
from app.modules.users.models import User
from tests.admin_auth_factories import KEY_SET, bearer, make_staff
from tests.auth_factories import (
    LOGOUT,
    ORIGIN,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    sha256,
)
from tests.conftest import TEST_JWT_ISSUER
from tests.rbac_factories import assign, make_role

V1 = "/api/v1"
TOKEN = f"{V1}/auth/token"
ORGS = f"{V1}/organizations"
ME = f"{V1}/me/permissions"
RESET = f"{V1}/auth/password/reset"
UNAUTHORIZED = {"code": "unauthorized", "message": "Authentication required", "details": None}
INVALID_CHALLENGE = 'Bearer realm="admin", error="invalid_token"'


@pytest.fixture(autouse=True)
async def _admin_origin(db_session: AsyncSession) -> None:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.origins = [ORIGIN]
    await db_session.flush()


async def _mint(client: AsyncClient, raw: str, account_id: uuid.UUID) -> str:
    resp = await client.post(
        TOKEN,
        json={"audience": "admin", "account_id": str(account_id)},
        headers=auth_headers(app_key="admin", cookie=raw),
    )
    assert resp.status_code == 200, resp.text
    token: str = resp.json()["access_token"]
    return token


async def _issued(client: AsyncClient, db: AsyncSession, user: User) -> tuple[str, uuid.UUID, str]:
    raw, _, (account,) = await make_device(db, user)
    return raw, account.id, await _mint(client, raw, account.id)


async def _alive(client: AsyncClient, token: str) -> None:
    for path in (ORGS, ME):
        assert (await client.get(path, headers=bearer(token))).status_code == 200


async def _dead(client: AsyncClient, token: str) -> None:
    for path in (ORGS, ME):
        resp: Response = await client.get(path, headers=bearer(token))
        assert resp.status_code == 401, resp.text
        assert resp.json() == {"error": UNAUTHORIZED}
        assert resp.headers["www-authenticate"] == INVALID_CHALLENGE


async def _fresh_works(client: AsyncClient, db: AsyncSession, user: User) -> None:
    _, _, token = await _issued(client, db, user)
    await _alive(client, token)


async def test_logout_account_ends_admin_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_staff(db_session, "admin:*")
    raw, account_id, token = await _issued(client, db_session, user)
    await _alive(client, token)
    resp = await client.post(
        LOGOUT,
        json={"account_id": str(account_id)},
        headers=auth_headers(app_key="admin", cookie=raw),
    )
    assert resp.status_code == 204, resp.text
    await _dead(client, token)
    await _fresh_works(client, db_session, user)


async def test_logout_all_ends_admin_token(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_staff(db_session, "admin:*")
    raw, _, token = await _issued(client, db_session, user)
    await _alive(client, token)
    resp = await client.post(
        LOGOUT, json={"all": True}, headers=auth_headers(app_key="admin", cookie=raw)
    )
    assert resp.status_code == 204, resp.text
    await _dead(client, token)
    await _fresh_works(client, db_session, user)


async def test_password_reset_ends_admin_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    user = await make_staff(db_session, "admin:*")
    _, _, token = await _issued(client, db_session, user)
    await _alive(client, token)
    raw_reset = secrets.token_urlsafe(32)
    await EmailTokenRepository(db_session).issue(user.id, "reset", sha256(raw_reset), RESET_TTL)
    resp = await client.post(
        RESET, json={"token": raw_reset, "new_password": "a brand new passphrase 42"}
    )
    assert resp.status_code == 204, resp.text
    await _dead(client, token)
    await _fresh_works(client, db_session, user)


async def test_staff_revoke_one_ends_admin_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_staff(db_session, "admin:*")
    target = await make_staff(db_session, "admin:organizations:read")
    _, account_id, token = await _issued(client, db_session, target)
    await _alive(client, token)
    resp = await client.delete(
        f"{V1}/users/{target.id}/sessions/{account_id}", headers=bearer(_token_of(actor))
    )
    assert resp.status_code == 204, resp.text
    await _dead(client, token)
    await _fresh_works(client, db_session, target)


async def test_staff_revoke_all_ends_admin_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_staff(db_session, "admin:*")
    target = await make_staff(db_session, "admin:organizations:read")
    _, _, token = await _issued(client, db_session, target)
    await _alive(client, token)
    resp = await client.delete(f"{V1}/users/{target.id}/sessions", headers=bearer(_token_of(actor)))
    assert resp.status_code == 204, resp.text
    await _dead(client, token)
    await _fresh_works(client, db_session, target)


async def test_disable_then_reenable_ends_admin_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_staff(db_session, "admin:*")
    target = await make_staff(db_session, "admin:organizations:read")
    _, _, token = await _issued(client, db_session, target)
    await _alive(client, token)
    headers = bearer(_token_of(actor))
    for status in ("disabled", "active"):
        resp = await client.patch(
            f"{V1}/users/{target.id}", json={"status": status}, headers=headers
        )
        assert resp.status_code == 200, resp.text
    await _dead(client, token)
    await _fresh_works(client, db_session, target)


async def test_other_session_of_same_user_survives(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_staff(db_session, "admin:*")
    target = await make_staff(db_session, "admin:organizations:read")
    _, account_a, token_a = await _issued(client, db_session, target)
    _, _, token_b = await _issued(client, db_session, target)
    resp = await client.delete(
        f"{V1}/users/{target.id}/sessions/{account_a}", headers=bearer(_token_of(actor))
    )
    assert resp.status_code == 204, resp.text
    await _dead(client, token_a)
    await _alive(client, token_b)


async def test_shared_device_other_user_survives(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    a = await make_staff(db_session, "admin:*")
    b = await make_staff(db_session, "admin:*")
    raw, _, (acc_a, acc_b) = await make_device(db_session, a, b)
    token_a = await _mint(client, raw, acc_a.id)
    token_b = await _mint(client, raw, acc_b.id)
    resp = await client.post(
        LOGOUT,
        json={"account_id": str(acc_a.id)},
        headers=auth_headers(app_key="admin", cookie=raw),
    )
    assert resp.status_code == 204, resp.text
    await _dead(client, token_a)
    await _alive(client, token_b)


async def test_revoke_between_session_read_and_sign_401(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await make_staff(db_session, "admin:*")
    raw, _, (account,) = await make_device(db_session, user)
    original = SessionRepository.get_active_account

    async def racing(self: SessionRepository, *args: Any, **kwargs: Any) -> Any:
        found = await original(self, *args, **kwargs)
        await db_session.execute(
            text("UPDATE account_sessions SET revoked_at = now() WHERE id = :id"),
            {"id": account.id},
        )
        return found

    monkeypatch.setattr(SessionRepository, "get_active_account", racing)
    token = await _mint(client, raw, account.id)
    await _dead(client, token)


async def test_consumer_token_has_no_sid_and_survives_logout(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_login_app(db_session)
    role = await make_role(db_session, app, "stoktok:member")
    user = await make_login_user(db_session)
    await assign(db_session, user, role)
    raw, _, (account,) = await make_device(db_session, user)
    resp = await client.post(
        TOKEN,
        json={"audience": "stoktok", "account_id": str(account.id)},
        headers=auth_headers(cookie=raw),
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    out = await client.post(LOGOUT, json={"all": True}, headers=auth_headers(cookie=raw))
    assert out.status_code == 204, out.text
    claims = verify_access_token(token, KEY_SET, audience="stoktok", issuer=TEST_JWT_ISSUER)
    assert "sid" not in claims


def _token_of(user: User) -> str:
    from tests.admin_auth_factories import mint

    return mint(user)
