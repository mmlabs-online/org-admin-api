import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import LoginFailure
from app.modules.sessions.models import AccountSession, DeviceSession
from tests.auth_factories import make_device, sha256
from tests.rbac_factories import make_user


async def test_token_hash_length_check(db_session: AsyncSession) -> None:
    db_session.add(DeviceSession(token_hash=b"short"))
    with pytest.raises(IntegrityError, match="ck_device_sessions_token_hash_length"):
        await db_session.flush()


async def test_prev_pair_check(db_session: AsyncSession) -> None:
    db_session.add(DeviceSession(token_hash=sha256("a"), prev_token_hash=sha256("b")))
    with pytest.raises(IntegrityError, match="ck_device_sessions_prev_pair"):
        await db_session.flush()


async def test_device_defaults(db_session: AsyncSession) -> None:
    _, device, _ = await make_device(db_session)
    row = (
        await db_session.execute(
            select(
                DeviceSession.created_at == func.now(),
                DeviceSession.last_seen_at == func.now(),
                DeviceSession.rotated_at == func.now(),
                DeviceSession.revoked_at,
            ).where(DeviceSession.id == device.id)
        )
    ).one()
    assert tuple(row) == (True, True, True, None)


async def test_one_active_account_session_per_device_user(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, device, (first,) = await make_device(db_session, user)
    async with db_session.begin_nested():
        db_session.add(AccountSession(device_session_id=device.id, user_id=user.id))
        with pytest.raises(IntegrityError, match="uq_account_sessions_active"):
            await db_session.flush()
    await db_session.execute(
        text("UPDATE account_sessions SET revoked_at = now() WHERE id = :id"), {"id": first.id}
    )
    db_session.add(AccountSession(device_session_id=device.id, user_id=user.id))
    await db_session.flush()  # a revoked row does not block a new one


async def _account_count(db_session: AsyncSession) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(AccountSession)) or 0)


async def test_device_delete_cascades_accounts(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, device, _ = await make_device(db_session, user)
    await db_session.execute(text("DELETE FROM device_sessions WHERE id = :id"), {"id": device.id})
    assert await _account_count(db_session) == 0


async def test_user_delete_cascades_accounts(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    await make_device(db_session, user)
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    assert await _account_count(db_session) == 0


async def test_login_failures_hmac_length_check(db_session: AsyncSession) -> None:
    db_session.add(LoginFailure(email_hmac=b"short", failures=1))
    with pytest.raises(IntegrityError, match="ck_login_failures_email_hmac_length"):
        await db_session.flush()


async def test_login_failures_failures_positive(db_session: AsyncSession) -> None:
    db_session.add(LoginFailure(email_hmac=uuid.uuid4().bytes * 2, failures=0))
    with pytest.raises(IntegrityError, match="ck_login_failures_failures_positive"):
        await db_session.flush()


async def test_no_refresh_tokens_table(db_session: AsyncSession) -> None:
    assert await db_session.scalar(text("SELECT to_regclass('refresh_tokens')")) is None
    assert await db_session.scalar(text("SELECT to_regclass('device_sessions')")) is not None
