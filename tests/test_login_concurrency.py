"""S1/S2 with the real `get_session`: committed data, explicit cleanup; not rolled back per test.

Each request gets its own pooled connection, so concurrent logins really race in Postgres.
Every wait is bounded by `TIMEOUT`, so a regression fails instead of hanging.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import SessionLocal
from app.main import create_app
from app.modules.auth.deps import get_turnstile_verifier
from app.modules.auth.models import LoginFailure
from app.modules.auth.passwords import PasswordHasher
from app.modules.auth.repository import FailureState, LoginFailureRepository
from app.modules.auth.throttle import EmailHasher
from app.modules.rbac.models import App
from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.users.models import User
from tests.auth_factories import (
    HMAC_KEY,
    LOGIN,
    ORIGIN,
    PASSWORD,
    login_body,
    make_login_app,
    make_login_user,
    recording_app,
)
from tests.fakes import FakeTurnstileVerifier

TIMEOUT = 10
WRONG = "wrong password entirely"


@dataclass
class Race:
    app: Any
    app_key: str
    user: User
    email_hmac: bytes

    def headers(self) -> dict[str, str]:
        return {"X-MM-App": self.app_key, "Origin": ORIGIN}

    async def post(self, password: str = WRONG, **extra: object) -> Response:
        transport = ASGITransport(app=self.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                LOGIN, json=login_body(self.user, password, **extra), headers=self.headers()
            )

    async def seed_failures(self, failures: int) -> None:
        async with SessionLocal() as session:
            await session.execute(
                text("INSERT INTO login_failures (email_hmac, failures) VALUES (:h, :f)"),
                {"h": self.email_hmac, "f": failures},
            )
            await session.commit()

    async def failure_row(self) -> LoginFailure | None:
        async with SessionLocal() as session:
            return await session.scalar(
                select(LoginFailure).where(LoginFailure.email_hmac == self.email_hmac)
            )


async def _bounded[T](awaitable: Awaitable[T]) -> T:
    return await asyncio.wait_for(awaitable, timeout=TIMEOUT)


async def _cleanup(session: AsyncSession, race_user_id: uuid.UUID, app_key: str, h: bytes) -> None:
    device_ids = (
        await session.scalars(
            select(AccountSession.device_session_id).where(AccountSession.user_id == race_user_id)
        )
    ).all()
    await session.execute(delete(LoginFailure).where(LoginFailure.email_hmac == h))
    await session.execute(delete(AccountSession).where(AccountSession.user_id == race_user_id))
    if device_ids:
        await session.execute(delete(DeviceSession).where(DeviceSession.id.in_(device_ids)))
    await session.execute(delete(User).where(User.id == race_user_id))
    await session.execute(delete(App).where(App.key == app_key))
    await session.commit()


@pytest.fixture
async def race() -> AsyncIterator[Race]:
    app_key = "race-" + uuid.uuid4().hex[:8]
    async with SessionLocal() as session:
        await make_login_app(session, key=app_key)
        user = await make_login_user(session)
        await session.commit()
    email_hmac = EmailHasher(HMAC_KEY).hmac(user.email.lower())
    app = create_app()
    turnstile = FakeTurnstileVerifier()
    app.dependency_overrides[get_turnstile_verifier] = lambda: turnstile
    try:
        yield Race(app, app_key, user, email_hmac)
    finally:
        async with SessionLocal() as session:
            await _cleanup(session, user.id, app_key, email_hmac)


def _hold_phase_one(monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """The first `parties` phase-1 reads wait for each other, so all reserve concurrently."""
    barrier = asyncio.Barrier(parties)
    real_get = LoginFailureRepository.get
    calls = 0

    async def get(self: LoginFailureRepository, email_hmac: bytes) -> FailureState:
        nonlocal calls
        state = await real_get(self, email_hmac)
        calls += 1
        if calls <= parties:
            await barrier.wait()
        return state

    monkeypatch.setattr(LoginFailureRepository, "get", get)


async def test_parallel_at_two_failures_one_401_one_turnstile_required(
    race: Race, monkeypatch: pytest.MonkeyPatch
) -> None:
    await race.seed_failures(2)
    _hold_phase_one(monkeypatch, 2)
    responses = await _bounded(asyncio.gather(race.post(), race.post()))
    by_status = sorted((r.status_code, r.json()["error"]["code"]) for r in responses)
    assert by_status == [(401, "invalid_credentials"), (403, "turnstile_required")]
    row = await race.failure_row()
    assert row is not None and row.failures == 3


async def test_parallel_at_four_failures_one_401_one_429(
    race: Race, monkeypatch: pytest.MonkeyPatch
) -> None:
    await race.seed_failures(4)
    _hold_phase_one(monkeypatch, 2)
    responses = await _bounded(
        asyncio.gather(race.post(turnstile_token="pass"), race.post(turnstile_token="pass"))
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [401, 429]
    limited = next(r for r in responses if r.status_code == 429)
    # `now()` is the transaction start: the loser's began before the winner's block was set,
    # so the 1-min block can read as up to 61 s (rounded up; overstating is fail-safe).
    assert 1 <= int(limited.headers["retry-after"]) <= 61
    row = await race.failure_row()
    assert row is not None and row.failures == 5
    assert row.blocked_until is not None


async def test_row_not_locked_during_password_check(
    race: Race, monkeypatch: pytest.MonkeyPatch
) -> None:
    await race.seed_failures(1)
    entered, release = asyncio.Event(), asyncio.Event()
    real_verify = PasswordHasher.verify

    async def verify(self: PasswordHasher, password_hash: str, password: str) -> bool:
        entered.set()
        await release.wait()
        return await real_verify(self, password_hash, password)

    monkeypatch.setattr(PasswordHasher, "verify", verify)
    attempt = asyncio.create_task(race.post())
    try:
        await _bounded(entered.wait())
        async with SessionLocal() as other:
            locked = await other.scalar(
                text("SELECT failures FROM login_failures WHERE email_hmac = :h FOR UPDATE NOWAIT"),
                {"h": race.email_hmac},
            )  # raises LockNotAvailable if the attempt still held the row
            assert locked == 2  # the reservation is already committed
            await other.rollback()
    finally:
        release.set()
    resp = await _bounded(attempt)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_real_get_session_commits_before_response_start(
    race: Race, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_commit = AsyncSession.commit

    async def commit(self: AsyncSession) -> None:
        await real_commit(self)
        events.append("commit")

    monkeypatch.setattr(AsyncSession, "commit", commit)
    race.app = recording_app(race.app, events)
    resp = await _bounded(race.post(PASSWORD))
    assert resp.status_code == 200, resp.text
    assert "set-cookie" in resp.headers
    assert events.count("commit") >= 2  # the reservation, then the request transaction
    assert events[-1] == "response.start"


async def _wait_for_lock_waiter() -> None:
    """Until some backend of this database waits on a lock (polled, bounded by the caller)."""
    while True:
        async with SessionLocal() as probe:
            waiting = await probe.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        if waiting:
            return
        await asyncio.sleep(0.02)


async def test_login_blocked_by_reset_lock_reads_new_password(race: Race) -> None:
    """AC 14: a reset holds `FOR UPDATE` on the user while it changes the hash; a login that
    reads the user `FOR SHARE` waits, then sees the new hash, so the old password fails."""
    new_hash = await PasswordHasher(Settings()).hash("a brand new passphrase 42")
    async with SessionLocal() as reset:
        try:
            await reset.execute(
                text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": race.user.id}
            )
            attempt = asyncio.create_task(race.post(PASSWORD))
            try:
                await _bounded(_wait_for_lock_waiter())
                assert not attempt.done()
                await reset.execute(
                    text("UPDATE users SET password_hash = :h WHERE id = :id"),
                    {"h": new_hash, "id": race.user.id},
                )
                await reset.commit()
            except BaseException:
                attempt.cancel()
                raise
        finally:
            await reset.rollback()
    resp = await _bounded(attempt)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"
    async with SessionLocal() as session:
        accounts = await session.scalar(
            select(func.count())
            .select_from(AccountSession)
            .where(AccountSession.user_id == race.user.id)
        )
    assert accounts == 0
