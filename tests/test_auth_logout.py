import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.sessions.service import DeviceSessionService
from tests.auth_factories import (
    LOGOUT,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    parse_set_cookie,
    shift,
)


@pytest.fixture(autouse=True)
async def _app(db_session: AsyncSession) -> None:
    await make_login_app(db_session)


async def _revoked(db_session: AsyncSession, model: type[Any], row_id: uuid.UUID) -> bool:
    row = await db_session.scalar(
        select(model).where(model.id == row_id).execution_options(populate_existing=True)
    )
    assert row is not None
    return row.revoked_at is not None


async def test_logout_one_keeps_other_account(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    raw, device, (a, b) = await make_device(db_session, alice, bob)
    resp = await client.post(
        LOGOUT, json={"account_id": str(a.id)}, headers=auth_headers(cookie=raw)
    )
    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert resp.headers["cache-control"] == "no-store"
    assert parse_set_cookie(resp) is None
    assert await _revoked(db_session, AccountSession, a.id)
    assert not await _revoked(db_session, AccountSession, b.id)
    assert not await _revoked(db_session, DeviceSession, device.id)


async def test_logout_foreign_account_is_noop(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, _, _ = await make_device(db_session, user)
    _, _, (foreign,) = await make_device(db_session, user)
    resp = await client.post(
        LOGOUT, json={"account_id": str(foreign.id)}, headers=auth_headers(cookie=raw)
    )
    assert resp.status_code == 204
    assert not await _revoked(db_session, AccountSession, foreign.id)


async def test_logout_all_revokes_device_and_clears_cookie(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    raw, device, (a, b) = await make_device(db_session, alice, bob)
    resp = await client.post(LOGOUT, json={"all": True}, headers=auth_headers(cookie=raw))
    assert resp.status_code == 204
    assert resp.headers["cache-control"] == "no-store"
    morsel = parse_set_cookie(resp)
    assert morsel is not None
    assert morsel.value == ""
    assert morsel["max-age"] == "0"
    assert morsel["path"] == "/"
    assert morsel["domain"] == ""
    assert await _revoked(db_session, DeviceSession, device.id)
    assert await _revoked(db_session, AccountSession, a.id)
    assert await _revoked(db_session, AccountSession, b.id)

    again = await client.post(LOGOUT, json={"all": True}, headers=auth_headers(cookie=raw))
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "session_invalid"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"account_id": str(uuid.uuid4()), "all": True},
        {"all": False},
        {"all": None},
        {"account_id": "not-a-uuid"},
        {"account_id": str(uuid.uuid4()), "extra": 1},
    ],
)
async def test_logout_body_validation(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    user = await make_login_user(db_session)
    raw, _, _ = await make_device(db_session, user)
    resp = await client.post(LOGOUT, json=body, headers=auth_headers(cookie=raw))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_logout_without_cookie_401(client: AsyncClient) -> None:
    resp = await client.post(LOGOUT, json={"all": True}, headers=auth_headers())
    assert resp.status_code == 401
    assert resp.json() == {
        "error": {"code": "session_invalid", "message": "Session is invalid", "details": None}
    }
    assert parse_set_cookie(resp) is None  # nothing to clear


async def _rotated_with_newer_account(
    db_session: AsyncSession,
) -> tuple[str, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Device with alice (an hour old); rotated, then bob added. Returns the old raw value."""
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    raw, device, (alice_account,) = await make_device(db_session, alice)
    # Inside one test transaction now() is constant: move alice's session into the past.
    await shift(db_session, "account_sessions", "created_at", alice_account.id, "-1 hour")
    service = DeviceSessionService(db_session, idle_days=30, max_days=365)
    assert (await service.resolve([raw], force_rotate=True)).new_cookie is not None
    bob_account_id = await service.add_account(device.id, bob.id)  # created_at == rotated_at
    return raw, device.id, alice_account.id, bob_account_id


async def test_logout_prev_value_cannot_revoke_newer_account(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, device_id, alice_id, bob_id = await _rotated_with_newer_account(db_session)
    resp = await client.post(
        LOGOUT, json={"account_id": str(bob_id)}, headers=auth_headers(cookie=raw)
    )
    assert resp.status_code == 204, resp.text
    assert not await _revoked(db_session, AccountSession, bob_id)
    assert not await _revoked(db_session, AccountSession, alice_id)
    assert not await _revoked(db_session, DeviceSession, device_id)


async def test_logout_prev_value_revokes_older_account(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, device_id, alice_id, bob_id = await _rotated_with_newer_account(db_session)
    resp = await client.post(
        LOGOUT, json={"account_id": str(alice_id)}, headers=auth_headers(cookie=raw)
    )
    assert resp.status_code == 204, resp.text
    assert await _revoked(db_session, AccountSession, alice_id)
    assert not await _revoked(db_session, AccountSession, bob_id)
    assert not await _revoked(db_session, DeviceSession, device_id)


async def test_logout_all_prev_value_revokes_device(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, device_id, alice_id, bob_id = await _rotated_with_newer_account(db_session)
    resp = await client.post(LOGOUT, json={"all": True}, headers=auth_headers(cookie=raw))
    assert resp.status_code == 204, resp.text
    morsel = parse_set_cookie(resp)
    assert morsel is not None and morsel.value == "" and morsel["max-age"] == "0"
    assert await _revoked(db_session, DeviceSession, device_id)
    assert await _revoked(db_session, AccountSession, alice_id)
    assert await _revoked(db_session, AccountSession, bob_id)
