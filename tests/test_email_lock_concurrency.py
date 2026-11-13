"""USR-04d S1: reset/verify vs a concurrent resend on real Postgres (committed data, cleanup).

Interleave: resend locks the old verify token X (`invalidate`), pauses before its INSERT; the
other request locks the user (`get_for_update`), then waits on X. Releasing resend makes its
INSERT take the FK `KEY SHARE` on the user row: with `FOR UPDATE` that closes a deadlock cycle,
with `FOR NO KEY UPDATE` it does not. Every wait is bounded by `TIMEOUT`.
"""

import asyncio
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select, text

from app.db.session import SessionLocal
from app.main import create_app
from app.modules.auth.deps import get_email_dispatcher, get_turnstile_verifier
from app.modules.auth.email_verification import VERIFY, VERIFY_TTL, email_token_hash
from app.modules.auth.models import EmailSendLog, EmailToken
from app.modules.auth.password_reset import RESET, RESET_TTL
from app.modules.auth.repository import EmailRetentionRepository, EmailTokenRepository
from app.modules.auth.throttle import EmailHasher
from app.modules.email.dispatcher import EmailDispatcher
from app.modules.users.models import User
from app.modules.users.repository import UserRepository
from tests.auth_factories import (
    HMAC_KEY,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
)
from tests.fakes import FakeEmailSender, FakeTurnstileVerifier
from tests.test_login_concurrency import TIMEOUT, _bounded, _cleanup, _wait_for_lock_waiter

RESEND = "/api/v1/auth/verify-email/resend"
VERIFY_URL = "/api/v1/auth/verify-email"
RESET_URL = "/api/v1/auth/password/reset"
NEW_PASSWORD = "a brand new passphrase 42"


@dataclass
class EmailRace:
    app: Any
    app_key: str
    user: User
    cookie: str
    account_id: uuid.UUID
    verify_raw: str
    reset_raw: str
    sender: FakeEmailSender

    async def post(self, url: str, body: dict[str, object], **headers: str) -> Response:
        transport = ASGITransport(app=self.app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(url, json=body, headers=headers)

    async def resend(self) -> Response:
        headers = auth_headers(self.app_key, cookie=self.cookie)
        return await self.post(RESEND, {"account_id": str(self.account_id)}, **headers)

    async def reset(self) -> Response:
        return await self.post(RESET_URL, {"token": self.reset_raw, "new_password": NEW_PASSWORD})

    async def verify(self) -> Response:
        return await self.post(VERIFY_URL, {"token": self.verify_raw})


@pytest.fixture
async def email_race() -> AsyncIterator[EmailRace]:
    app_key = "erace-" + uuid.uuid4().hex[:8]
    verify_raw, reset_raw = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    async with SessionLocal() as session:
        await make_login_app(session, key=app_key)
        user = await make_login_user(session, verified=False)
        cookie, _, (account,) = await make_device(session, user)
        tokens = EmailTokenRepository(session)
        await tokens.issue(user.id, VERIFY, email_token_hash(verify_raw), VERIFY_TTL)
        await tokens.issue(user.id, RESET, email_token_hash(reset_raw), RESET_TTL)
        await session.commit()
    email_hmac = EmailHasher(HMAC_KEY).hmac(user.email.lower())
    app = create_app()
    turnstile = FakeTurnstileVerifier()
    sender = FakeEmailSender()
    dispatcher = EmailDispatcher(sender)
    app.dependency_overrides[get_turnstile_verifier] = lambda: turnstile
    app.dependency_overrides[get_email_dispatcher] = lambda: dispatcher
    try:
        yield EmailRace(app, app_key, user, cookie, account.id, verify_raw, reset_raw, sender)
    finally:
        await dispatcher.aclose(timeout=0)
        async with SessionLocal() as session:
            await session.execute(delete(EmailSendLog).where(EmailSendLog.email_hmac == email_hmac))
            await _cleanup(session, user.id, app_key, email_hmac)


def _pause_resend_before_insert(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """Resend's `issue` locks the old verify token, then waits for `release` before the INSERT."""
    paused, release = asyncio.Event(), asyncio.Event()
    real_issue = EmailTokenRepository.issue

    async def issue(
        self: EmailTokenRepository,
        user_id: uuid.UUID,
        purpose: str,
        token_hash: bytes,
        ttl: timedelta,
    ) -> None:
        if purpose == VERIFY and not paused.is_set():
            await self.invalidate(user_id, purpose)  # resend now holds X's row lock
            paused.set()
            await release.wait()
        await real_issue(self, user_id, purpose, token_hash, ttl)  # INSERT -> FK KEY SHARE

    monkeypatch.setattr(EmailTokenRepository, "issue", issue)
    return paused, release


async def _race(email_race: EmailRace, monkeypatch: pytest.MonkeyPatch, other: str) -> list[int]:
    """(resend, other) status codes for the interleave described in the module docstring."""
    paused, release = _pause_resend_before_insert(monkeypatch)
    tasks = [asyncio.create_task(email_race.resend())]
    try:
        await _bounded(paused.wait())
        tasks.append(
            asyncio.create_task(email_race.reset() if other == "reset" else email_race.verify())
        )
        await _bounded(_wait_for_lock_waiter())
        assert not tasks[1].done()
    except BaseException:
        release.set()  # let both requests finish before the fixture cleans up
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), TIMEOUT)
        raise
    release.set()
    responses = await _bounded(asyncio.gather(*tasks))
    return [r.status_code for r in responses]


async def _user_row(user_id: uuid.UUID) -> User:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
    assert user is not None
    return user


async def test_resend_vs_reset_no_deadlock(
    email_race: EmailRace, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_hash = email_race.user.password_hash
    assert await _race(email_race, monkeypatch, "reset") == [202, 204]
    user = await _user_row(email_race.user.id)
    assert user.email_verified_at is not None
    assert user.password_hash != old_hash
    async with SessionLocal() as session:
        old_used = await session.scalar(
            select(EmailToken.used_at).where(
                EmailToken.token_hash == email_token_hash(email_race.verify_raw)
            )
        )
    assert old_used is not None


async def test_resend_vs_verify_no_deadlock(
    email_race: EmailRace, monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses = await _race(email_race, monkeypatch, "verify")
    assert statuses == [202, 422]
    user = await _user_row(email_race.user.id)
    assert user.email_verified_at is None
    async with SessionLocal() as session:
        new_live = await session.scalar(
            select(func.count())
            .select_from(EmailToken)
            .where(
                EmailToken.user_id == email_race.user.id,
                EmailToken.purpose == VERIFY,
                EmailToken.token_hash != email_token_hash(email_race.verify_raw),
                EmailToken.used_at.is_(None),
                EmailToken.expires_at > func.now(),
            )
        )
    assert new_live == 1


@pytest.mark.parametrize("other", ["reset", "verify"])
async def test_control_plain_for_update_deadlocks(
    email_race: EmailRace, monkeypatch: pytest.MonkeyPatch, other: str
) -> None:
    """The same interleave with the old `FOR UPDATE` reaches the S1 cycle: Postgres aborts one
    transaction (`deadlock_timeout`, 1 s), which answers 500."""

    async def plain_for_update(self: UserRepository, user_id: uuid.UUID) -> User | None:
        return await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    monkeypatch.setattr(UserRepository, "get_for_update", plain_for_update)
    statuses = await _race(email_race, monkeypatch, other)
    assert 500 in statuses


async def test_purge_skips_locked_rows() -> None:
    row_id = uuid.uuid4()
    async with SessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO email_send_log (id, email_hmac, purpose, sent_at) VALUES "
                "(:id, :h, 'verify', now() - interval '3 days')"
            ),
            {"id": row_id, "h": email_token_hash(row_id.hex)},
        )
        await session.commit()
    try:
        async with SessionLocal() as holder:
            await holder.execute(
                text("SELECT id FROM email_send_log WHERE id = :id FOR UPDATE"), {"id": row_id}
            )
            async with SessionLocal() as session:
                repo = EmailRetentionRepository(session)
                await repo.set_lock_timeout()
                assert await _bounded(repo.purge_send_log(timedelta(hours=48), 10)) == 0
                await repo.commit()
            await holder.rollback()
        async with SessionLocal() as session:
            repo = EmailRetentionRepository(session)
            assert await _bounded(repo.purge_send_log(timedelta(hours=48), 10)) == 1
            await repo.commit()
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(EmailSendLog).where(EmailSendLog.id == row_id))
            await session.commit()
