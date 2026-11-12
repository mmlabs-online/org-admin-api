import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import (
    DeviceSessionService,
    device_token_hash,
    new_device_token,
)
from tests.auth_factories import (
    LOGIN,
    LOGOUT,
    auth_headers,
    login_body,
    make_device,
    make_login_app,
    make_login_user,
    parse_set_cookie,
    sha256,
    shift,
)
from tests.conftest import MakeClient


@pytest.fixture(autouse=True)
async def _app(db_session: AsyncSession) -> None:
    await make_login_app(db_session)


def _service(db_session: AsyncSession) -> DeviceSessionService:
    return DeviceSessionService(db_session, idle_days=30, max_days=365)


async def _device(db_session: AsyncSession, device_id: uuid.UUID) -> DeviceSession:
    device = await db_session.scalar(
        select(DeviceSession)
        .where(DeviceSession.id == device_id)
        .execution_options(populate_existing=True)
    )
    assert device is not None
    return device


async def _with_prev(db_session: AsyncSession, device: DeviceSession, delta: str) -> None:
    """Make the device's cookie the previous value; its grace ends at now() + delta."""
    await db_session.execute(
        text(
            "UPDATE device_sessions SET prev_token_hash = token_hash, token_hash = :new, "
            "prev_valid_until = now() + CAST(CAST(:d AS text) AS interval) WHERE id = :id"
        ),
        {"new": sha256("current-" + uuid.uuid4().hex), "d": delta, "id": device.id},
    )


def _logout(account_id: uuid.UUID | None = None) -> dict[str, str]:
    return {"account_id": str(account_id or uuid.uuid4())}


async def test_rotates_after_24h(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    resp = await client.post(LOGOUT, json=_logout(), headers=auth_headers(cookie=raw))
    assert resp.status_code == 204, resp.text
    morsel = parse_set_cookie(resp)
    assert morsel is not None and morsel.value != raw
    stored = await _device(db_session, device.id)
    assert stored.token_hash == sha256(morsel.value)
    assert stored.prev_token_hash == sha256(raw)
    assert await db_session.scalar(
        select(DeviceSession.prev_valid_until == func.now() + text("interval '60 seconds'")).where(
            DeviceSession.id == device.id
        )
    )


async def test_no_rotation_before_24h(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-23 hours")
    resp = await client.post(LOGOUT, json=_logout(), headers=auth_headers(cookie=raw))
    assert resp.status_code == 204
    assert parse_set_cookie(resp) is None
    assert (await _device(db_session, device.id)).token_hash == sha256(raw)


async def test_prev_value_within_grace_accepted_without_rotation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await _with_prev(db_session, device, "60 seconds")
    current = (await _device(db_session, device.id)).token_hash

    resp = await client.post(LOGOUT, json=_logout(), headers=auth_headers(cookie=raw))
    assert resp.status_code == 204
    assert parse_set_cookie(resp) is None

    login = await client.post(LOGIN, json=login_body(user), headers=auth_headers(cookie=raw))
    assert login.status_code == 200
    assert parse_set_cookie(login) is None  # no re-rotation, also on login
    stored = await _device(db_session, device.id)
    assert stored.token_hash == current
    assert stored.revoked_at is None


async def _revocations(db_session: AsyncSession, device_id: uuid.UUID) -> tuple[bool, int]:
    revoked = await db_session.scalar(
        text("SELECT revoked_at IS NOT NULL FROM device_sessions WHERE id = :id"), {"id": device_id}
    )
    active = await db_session.scalar(
        text(
            "SELECT count(*) FROM account_sessions "
            "WHERE device_session_id = :id AND revoked_at IS NULL"
        ),
        {"id": device_id},
    )
    return bool(revoked), int(active or 0)


async def test_reuse_after_grace_revokes_device(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await _with_prev(db_session, device, "-1 second")
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(LOGOUT, json=_logout(), headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"
    morsel = parse_set_cookie(resp)
    assert morsel is not None and morsel["max-age"] == "0"
    assert await _revocations(db_session, device.id) == (True, 0)


async def test_reuse_revocation_needs_commit(
    make_strict_client: MakeClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_commit(self: SessionRepository) -> None:
        return None

    monkeypatch.setattr(SessionRepository, "commit", no_commit)
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await _with_prev(db_session, device, "-1 second")
    device_id = device.id  # the rollback below expires ORM objects
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(LOGOUT, json=_logout(), headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert await _revocations(db_session, device_id) == (False, 1)


async def test_concurrent_rotation_cas_loser_accepted(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    service = _service(db_session)
    original = service.repo.find_device
    calls = 0

    async def racing(token_hash: bytes) -> tuple[DeviceSession, object] | None:
        nonlocal calls
        found = await original(token_hash)
        calls += 1
        if calls == 1:  # another request rotates between our read and our CAS
            winner = device_token_hash(new_device_token())
            assert await service.repo.rotate(device.id, token_hash, winner)
        return found  # type: ignore[return-value]

    monkeypatch.setattr(service.repo, "find_device", racing)
    resolved = await service.resolve([raw], force_rotate=True)
    assert calls == 2
    assert resolved.device_id == device.id
    assert resolved.new_cookie is None
    assert resolved.accounts_created_before is not None
    assert await _revocations(db_session, device.id) == (False, 1)


async def test_login_on_empty_device_mints_new_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session)  # no account sessions
    resp = await client.post(LOGIN, json=login_body(user), headers=auth_headers(cookie=raw))
    assert resp.status_code == 200
    morsel = parse_set_cookie(resp)
    assert morsel is not None
    new = await db_session.scalar(
        select(DeviceSession).where(DeviceSession.token_hash == sha256(morsel.value))
    )
    assert new is not None and new.id != device.id
    assert (await _device(db_session, device.id)).revoked_at is not None


async def test_prev_value_cannot_use_accounts_added_after_rotation(
    db_session: AsyncSession,
) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    raw, device, (alice_account,) = await make_device(db_session, alice)
    # Inside one test transaction now() is constant: move the existing rows into the past.
    await shift(db_session, "account_sessions", "created_at", alice_account.id, "-1 hour")
    service = _service(db_session)
    rotated = await service.resolve([raw], force_rotate=True)
    assert rotated.new_cookie is not None
    bob_account_id = await service.add_account(device.id, bob.id)

    via_prev = await service.resolve([raw])
    assert via_prev.new_cookie is None
    before = via_prev.accounts_created_before
    assert before is not None
    repo = service.repo
    assert await repo.get_active_account(device.id, alice_account.id, created_before=before)
    assert await repo.get_active_account(device.id, bob_account_id, created_before=before) is None
    assert await repo.get_active_account(device.id, bob_account_id, created_before=None)


async def test_multiple_device_cookies_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    headers = auth_headers() | {"Cookie": f"mm_device={raw}; mm_device=other"}
    resp = await client.post(LOGOUT, json=_logout(), headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"
    assert await _revocations(db_session, device.id) == (False, 1)


async def _assert_unusable(
    client: AsyncClient, db_session: AsyncSession, column: str, delta: str
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await shift(db_session, "device_sessions", column, device.id, delta)
    resp = await client.post(LOGOUT, json=_logout(), headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"

    # Login never 401s on a bad cookie: it starts a new device session.
    login = await client.post(LOGIN, json=login_body(user), headers=auth_headers(cookie=raw))
    assert login.status_code == 200
    morsel = parse_set_cookie(login)
    assert morsel is not None
    new = await db_session.scalar(
        select(DeviceSession.id).where(DeviceSession.token_hash == sha256(morsel.value))
    )
    assert new is not None and new != device.id


async def test_revoked_device_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    await _assert_unusable(client, db_session, "revoked_at", "0 seconds")


async def test_idle_expired_device_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    await _assert_unusable(client, db_session, "last_seen_at", "-31 days")


async def test_absolute_expired_device_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _assert_unusable(client, db_session, "created_at", "-366 days")


async def test_account_session_unique_while_active(db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    _, device, (account,) = await make_device(db_session, user)
    service = _service(db_session)
    assert await service.add_account(device.id, user.id) == account.id
    count = await db_session.scalar(
        select(func.count()).select_from(AccountSession).where(AccountSession.user_id == user.id)
    )
    assert count == 1
