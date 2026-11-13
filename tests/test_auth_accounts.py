"""GET /api/v1/auth/accounts (USR-03b): the device's signed-in accounts, cookie class."""

import logging
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App
from app.modules.sessions.models import AccountSession
from app.modules.sessions.service import DeviceSessionService
from tests.auth_factories import (
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    parse_set_cookie,
    shift,
)
from tests.conftest import MakeClient

ACCOUNTS = "/api/v1/auth/accounts"


@pytest.fixture
async def stoktok(db_session: AsyncSession) -> App:
    return await make_login_app(db_session)


def _error(resp: Any) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


def _ids(resp: Any) -> list[str]:
    return [item["account_id"] for item in resp.json()["items"]]


async def test_accounts_lists_own_device(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    raw, _, (a, b) = await make_device(db_session, alice, bob)
    await shift(db_session, "account_sessions", "created_at", b.id, "-1 hour")  # bob first
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert resp.status_code == 200, resp.text
    assert _ids(resp) == [str(b.id), str(a.id)]


async def test_accounts_fields_exact(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    user = await make_login_user(db_session, verified=False)
    raw, _, (account,) = await make_device(db_session, user)
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "items": [
            {
                "account_id": str(account.id),
                "email": user.email,
                "email_verified": False,
                "display_name": None,
            }
        ]
    }
    assert str(user.id) not in resp.text

    user.display_name = "Alice"
    user.email_verified_at = func.now()
    await db_session.flush()
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    (item,) = resp.json()["items"]
    assert (item["display_name"], item["email_verified"]) == ("Alice", True)


async def test_accounts_excludes_other_device(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    raw, _, (a,) = await make_device(db_session, alice)
    await make_device(db_session, bob)
    await make_device(db_session, alice)  # alice on another device too
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert resp.status_code == 200
    assert _ids(resp) == [str(a.id)]


async def _revoke(db_session: AsyncSession, account_id: uuid.UUID) -> None:
    await db_session.execute(
        update(AccountSession)
        .where(AccountSession.id == account_id)
        .values(revoked_at=func.now())
        .execution_options(synchronize_session=False)
    )


async def test_accounts_excludes_revoked(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    raw, _, (a, b) = await make_device(db_session, alice, bob)
    await _revoke(db_session, a.id)
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert _ids(resp) == [str(b.id)]


async def test_accounts_hides_disabled_user(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    alice = await make_login_user(db_session, status="disabled")
    bob = await make_login_user(db_session)
    raw, _, (_, b) = await make_device(db_session, alice, bob)
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert _ids(resp) == [str(b.id)]


async def test_accounts_empty_device(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    user = await make_login_user(db_session)
    raw, _, (account,) = await make_device(db_session, user)
    await _revoke(db_session, account.id)
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert resp.status_code == 200
    assert resp.json() == {"items": []}
    assert parse_set_cookie(resp) is None


async def test_accounts_previous_cookie_hides_newer_account(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    raw, device, (alice_account,) = await make_device(db_session, alice)
    await shift(db_session, "account_sessions", "created_at", alice_account.id, "-1 hour")
    service = DeviceSessionService(db_session, idle_days=30, max_days=365)
    new = (await service.resolve([raw], force_rotate=True)).new_cookie
    assert new is not None
    bob_id = await service.add_account(device.id, bob.id)  # created_at == rotated_at

    previous = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert previous.status_code == 200
    assert _ids(previous) == [str(alice_account.id)]
    assert parse_set_cookie(previous) is None
    current = await client.get(ACCOUNTS, headers=auth_headers(cookie=new))
    assert _ids(current) == [str(alice_account.id), str(bob_id)]


async def test_accounts_no_store_and_rotation(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    user = await make_login_user(db_session)
    raw, device, (account,) = await make_device(db_session, user)
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert resp.headers["cache-control"] == "no-store"
    assert parse_set_cookie(resp) is None

    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    morsel = parse_set_cookie(resp)
    assert morsel is not None and morsel.value and morsel.value != raw
    assert _ids(resp) == [str(account.id)]


async def test_accounts_missing_cookie_401(client: AsyncClient, stoktok: App) -> None:
    resp = await client.get(ACCOUNTS, headers=auth_headers())
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"


async def test_accounts_unknown_cookie_401(client: AsyncClient, stoktok: App) -> None:
    resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=uuid.uuid4().hex * 2))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"


async def test_accounts_missing_app_header_403(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    resp = await client.get(ACCOUNTS, headers=auth_headers(app_key=None, cookie=raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "app_header_required"
    assert parse_set_cookie(resp) is None  # rejected before the device lookup


async def test_accounts_bad_origin_403(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    for origin in ("https://evil.example", None):
        resp = await client.get(ACCOUNTS, headers=auth_headers(origin=origin, cookie=raw))
        assert resp.status_code == 403
        assert _error(resp)["code"] == "origin_not_allowed"
        assert parse_set_cookie(resp) is None


async def test_accounts_rate_limited_429(
    make_client: MakeClient, db_session: AsyncSession, stoktok: App
) -> None:
    user = await make_login_user(db_session)
    raw, _, _ = await make_device(db_session, user)
    async with make_client(accounts_rate_limit=1) as client:
        first = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
        assert first.status_code == 200
        second = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert second.status_code == 429
    assert _error(second)["code"] == "rate_limited"
    assert "retry-after" in second.headers


async def test_accounts_logs_no_email(
    client: AsyncClient,
    db_session: AsyncSession,
    stoktok: App,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The migrations' `fileConfig` (session fixture) disables loggers that already exist.
    monkeypatch.setattr(logging.getLogger("app.modules.sessions.service"), "disabled", False)
    user = await make_login_user(db_session)
    raw, _, _ = await make_device(db_session, user)
    with caplog.at_level(logging.DEBUG):
        resp = await client.get(ACCOUNTS, headers=auth_headers(cookie=raw))
    assert resp.status_code == 200
    assert user.email not in caplog.text
    assert raw not in caplog.text
