import logging
import re
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.passwords import PasswordHasher
from app.modules.sessions.models import AccountSession, DeviceSession
from tests.auth_factories import (
    LOGIN,
    PASSWORD,
    auth_headers,
    login_body,
    make_login_app,
    make_login_user,
    parse_set_cookie,
    sha256,
)


@pytest.fixture(autouse=True)
async def _app(db_session: AsyncSession) -> None:
    await make_login_app(db_session)


async def _active_accounts(db_session: AsyncSession, device_id: object) -> list[AccountSession]:
    return list(
        await db_session.scalars(
            select(AccountSession)
            .where(
                AccountSession.device_session_id == device_id, AccountSession.revoked_at.is_(None)
            )
            .execution_options(populate_existing=True)
        )
    )


async def _device_for(db_session: AsyncSession, cookie: str) -> DeviceSession:
    device = await db_session.scalar(
        select(DeviceSession)
        .where(DeviceSession.token_hash == sha256(cookie))
        .execution_options(populate_existing=True)
    )
    assert device is not None
    return device


async def test_login_sets_device_cookie_attributes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    resp = await client.post(LOGIN, json=login_body(user), headers=auth_headers())
    assert resp.status_code == 200, resp.text
    morsel = parse_set_cookie(resp)
    assert morsel is not None
    assert morsel["httponly"] is True
    assert morsel["secure"] is True
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/api/v1/auth"
    assert morsel["domain"] == ""
    assert morsel["max-age"] == str(30 * 86400)
    assert len(morsel.value) >= 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", morsel.value)


async def test_only_cookie_hash_is_stored(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    resp = await client.post(LOGIN, json=login_body(user), headers=auth_headers())
    morsel = parse_set_cookie(resp)
    assert morsel is not None
    device = await _device_for(db_session, morsel.value)
    assert device.token_hash == sha256(morsel.value)
    row = (
        await db_session.execute(
            select(DeviceSession.__table__).where(DeviceSession.id == device.id)
        )
    ).one()
    for value in row:
        assert morsel.value not in str(value)
        assert not (isinstance(value, bytes) and morsel.value.encode() in value)


async def test_login_creates_device_and_account_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, verified=False)
    resp = await client.post(LOGIN, json=login_body(user), headers=auth_headers())
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert set(body) == {"account_id", "user"}
    assert body["user"] == {"id": str(user.id), "email": user.email, "email_verified": False}
    morsel = parse_set_cookie(resp)
    assert morsel is not None
    device = await _device_for(db_session, morsel.value)
    (account,) = await _active_accounts(db_session, device.id)
    assert str(account.id) == body["account_id"]
    assert account.user_id == user.id


async def test_login_again_reuses_account_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    first = await client.post(LOGIN, json=login_body(user), headers=auth_headers())
    c1 = parse_set_cookie(first)
    assert c1 is not None
    second = await client.post(LOGIN, json=login_body(user), headers=auth_headers(cookie=c1.value))
    assert second.status_code == 200
    c2 = parse_set_cookie(second)
    assert c2 is not None and c2.value != c1.value
    assert second.json()["account_id"] == first.json()["account_id"]
    device = await _device_for(db_session, c2.value)
    assert len(await _active_accounts(db_session, device.id)) == 1


async def test_two_accounts_on_one_device(client: AsyncClient, db_session: AsyncSession) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    first = await client.post(LOGIN, json=login_body(alice), headers=auth_headers())
    c1 = parse_set_cookie(first)
    assert c1 is not None
    device = await _device_for(db_session, c1.value)
    second = await client.post(LOGIN, json=login_body(bob), headers=auth_headers(cookie=c1.value))
    assert second.status_code == 200
    c2 = parse_set_cookie(second)
    assert c2 is not None
    assert (await _device_for(db_session, c2.value)).id == device.id
    accounts = {a.user_id: a for a in await _active_accounts(db_session, device.id)}
    assert set(accounts) == {alice.id, bob.id}
    assert first.json()["account_id"] != second.json()["account_id"]
    assert str(accounts[alice.id].id) == first.json()["account_id"]
    assert accounts[alice.id].revoked_at is None
    assert await db_session.scalar(select(func.count()).select_from(DeviceSession)) == 1


@pytest.mark.parametrize("case", ["unknown", "wrong_password", "disabled"])
async def test_invalid_credentials_identical_bodies(
    client: AsyncClient, db_session: AsyncSession, case: str
) -> None:
    user = await make_login_user(db_session, status="disabled" if case == "disabled" else "active")
    body: dict[str, Any] = login_body(user)
    if case == "unknown":
        body["email"] = "nobody@example.com"
    elif case == "wrong_password":
        body["password"] = "wrong password entirely"
    resp = await client.post(LOGIN, json=body, headers=auth_headers())
    assert resp.status_code == 401
    assert resp.json() == {
        "error": {
            "code": "invalid_credentials",
            "message": "Invalid email or password",
            "details": None,
        }
    }
    assert parse_set_cookie(resp) is None


async def test_unknown_email_runs_dummy_verify(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    original = PasswordHasher.verify_dummy

    async def spy(self: PasswordHasher, password: str) -> None:
        calls.append("dummy")
        await original(self, password)

    monkeypatch.setattr(PasswordHasher, "verify_dummy", spy)
    resp = await client.post(
        LOGIN, json={"email": "nobody@example.com", "password": PASSWORD}, headers=auth_headers()
    )
    assert resp.status_code == 401
    assert calls == ["dummy"]


async def test_login_with_unknown_cookie_creates_new_device(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    resp = await client.post(LOGIN, json=login_body(user), headers=auth_headers(cookie="garbage"))
    assert resp.status_code == 200
    morsel = parse_set_cookie(resp)
    assert morsel is not None and morsel.value != "garbage"
    await _device_for(db_session, morsel.value)


async def test_login_body_validation(client: AsyncClient) -> None:
    for body in (
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "a@example.com"},
        {"email": "a@example.com", "password": PASSWORD, "extra": 1},
        {"email": "a@example.com", "password": "x" * 129},
        {"email": "a@example.com", "password": PASSWORD, "turnstile_token": ""},
    ):
        resp = await client.post(LOGIN, json=body, headers=auth_headers())
        assert resp.status_code == 422, body
        assert resp.json()["error"]["code"] == "validation_error"


async def test_responses_never_echo_secrets(
    client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    user = await make_login_user(db_session)
    ok = await client.post(LOGIN, json=login_body(user), headers=auth_headers())
    morsel = parse_set_cookie(ok)
    assert morsel is not None
    bad = await client.post(
        LOGIN,
        json=login_body(user, "wrong password entirely", turnstile_token="tok-secret"),
        headers=auth_headers(cookie=morsel.value),
    )
    assert bad.status_code == 401
    for secret in (PASSWORD, "wrong password entirely", morsel.value, "tok-secret"):
        assert secret not in ok.text
        assert secret not in bad.text
        assert secret not in caplog.text
    assert user.email not in bad.text
    assert user.email not in caplog.text
    assert sha256(morsel.value).hex() not in caplog.text
