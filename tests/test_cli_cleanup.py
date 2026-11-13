"""Email retention cleanup CLI and service (USR-04d): what is deleted, batches, floors, output."""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import CLEANUP_USAGE, MSG_CLEANUP_FAILED, MSG_DB_ECHO, amain
from app.core.config import Settings
from app.modules.auth.models import EmailSendLog, EmailToken
from app.modules.auth.repository import EmailRetentionRepository
from app.modules.auth.retention import EmailRetentionService, PurgeCount
from tests.auth_factories import sha256
from tests.rbac_factories import make_user

Scope = Callable[[], AbstractAsyncContextManager[AsyncSession]]

DELETED = [
    "email_send_log: deleted 1 (older than 48 h)",
    "email_tokens: deleted 2 (used or expired, older than 7 d)",
]
WOULD_DELETE = [
    "email_send_log: would delete 1 (older than 48 h)",
    "email_tokens: would delete 2 (used or expired, older than 7 d)",
    "dry-run: nothing was deleted",
]


def _scope(db_session: AsyncSession) -> Scope:
    @asynccontextmanager
    async def _s() -> AsyncIterator[AsyncSession]:
        yield db_session  # the fixture owns the transaction; commit releases a savepoint

    return _s


@asynccontextmanager
async def _never() -> AsyncIterator[AsyncSession]:
    raise AssertionError("session scope must not be entered")
    yield  # pragma: no cover


@asynccontextmanager
async def _failing() -> AsyncIterator[AsyncSession]:
    raise OperationalError(
        "DELETE FROM email_tokens", {}, Exception("postgresql://admin:secret@db/org_admin")
    )
    yield  # pragma: no cover


def _assert_no_leak(out: str, err: str) -> None:
    both = out + err
    assert "Traceback" not in both
    assert "postgresql" not in both
    assert "secret" not in both
    assert "@" not in both


async def _send_log(db_session: AsyncSession, age: str) -> None:
    await db_session.execute(
        text(
            "INSERT INTO email_send_log (id, email_hmac, purpose, sent_at) VALUES "
            "(gen_random_uuid(), :h, 'verify', now() - CAST(CAST(:age AS text) AS interval))"
        ),
        {"h": sha256(uuid.uuid4().hex), "age": age},
    )


async def _token(
    db_session: AsyncSession, user_id: uuid.UUID, age: str, *, used: bool, ttl: str
) -> uuid.UUID:
    token_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO email_tokens (id, user_id, purpose, token_hash, created_at, expires_at,"
            " used_at) VALUES (:id, :u, 'verify', :h,"
            " now() - CAST(CAST(:age AS text) AS interval),"
            " now() - CAST(CAST(:age AS text) AS interval) + CAST(CAST(:ttl AS text) AS interval),"
            " CASE WHEN :used THEN now() - CAST(CAST(:age AS text) AS interval) END)"
        ),
        {
            "id": token_id,
            "u": user_id,
            "h": sha256(uuid.uuid4().hex),
            "age": age,
            "ttl": ttl,
            "used": used,
        },
    )
    return token_id


async def _seed(db_session: AsyncSession) -> dict[str, uuid.UUID]:
    await _send_log(db_session, "49 hours")  # deleted
    await _send_log(db_session, "47 hours")  # kept
    user = await make_user(db_session)
    return {
        "used_8d": await _token(db_session, user.id, "8 days", used=True, ttl="24 hours"),
        "expired_8d": await _token(db_session, user.id, "8 days", used=False, ttl="24 hours"),
        "used_25h": await _token(db_session, user.id, "25 hours", used=True, ttl="24 hours"),
        "live_8d": await _token(db_session, user.id, "8 days", used=False, ttl="30 days"),
    }


async def _counts(db_session: AsyncSession) -> tuple[int, int]:
    logs = await db_session.scalar(select(func.count()).select_from(EmailSendLog))
    tokens = await db_session.scalar(select(func.count()).select_from(EmailToken))
    return logs or 0, tokens or 0


async def _token_ids(db_session: AsyncSession) -> set[uuid.UUID]:
    return set((await db_session.scalars(select(EmailToken.id))).all())


async def _run(db_session: AsyncSession, *argv: str) -> int:
    return await amain(["cleanup", *argv], settings=Settings(), session_scope=_scope(db_session))


async def test_cleanup_deletes_old_rows_keeps_recent(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = await _seed(db_session)
    assert await _run(db_session) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == DELETED
    assert err == ""
    _assert_no_leak(out, err)
    assert await _counts(db_session) == (1, 2)
    assert await _token_ids(db_session) == {ids["used_25h"], ids["live_8d"]}
    kept_age = await db_session.scalar(select(func.now() - func.min(EmailSendLog.sent_at)))
    assert kept_age == timedelta(hours=47)


async def test_cleanup_dry_run_deletes_nothing(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _seed(db_session)
    assert await _run(db_session, "--dry-run") == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == WOULD_DELETE
    assert err == ""
    assert await _counts(db_session) == (2, 4)


async def test_cleanup_batches(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    for _ in range(5):
        await _send_log(db_session, "3 days")
    calls: list[int] = []
    real_purge = EmailRetentionRepository.purge_send_log

    async def purge(self: EmailRetentionRepository, older_than: timedelta, batch: int) -> int:
        deleted = await real_purge(self, older_than, batch)
        calls.append(deleted)
        return deleted

    monkeypatch.setattr(EmailRetentionRepository, "purge_send_log", purge)
    result = await EmailRetentionService(db_session, batch_size=2).run(dry_run=False)
    assert result[0] == PurgeCount("email_send_log", 5, "older than 48 h")
    assert calls == [2, 2, 1]
    assert await _counts(db_session) == (0, 0)


async def test_purge_refuses_age_below_floor(db_session: AsyncSession) -> None:
    repo = EmailRetentionRepository(db_session)
    young = timedelta(hours=23)
    with pytest.raises(ValueError, match="minimum"):
        await repo.purge_tokens(young, 10)
    with pytest.raises(ValueError, match="minimum"):
        await repo.purge_send_log(young, 10)
    with pytest.raises(ValueError, match="minimum"):
        await repo.count_tokens(young)
    with pytest.raises(ValueError, match="minimum"):
        await repo.count_send_log(young)
    assert await repo.count_send_log(timedelta(hours=24)) == 0  # the floor itself is allowed


async def test_cleanup_failure_exit_1_fixed_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert await amain(["cleanup"], settings=Settings(), session_scope=_failing) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_CLEANUP_FAILED + "\n"
    _assert_no_leak(out, err)


@pytest.mark.parametrize(
    "argv", [["--older-than", "1h"], ["--dry-run", "secret-value"], ["--dry"], ["--help"]]
)
async def test_cleanup_usage_error_64(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    assert await amain(["cleanup", *argv], settings=Settings(), session_scope=_never) == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == CLEANUP_USAGE + "\n"
    for echoed in ("--older-than", "1h", "secret-value", "--help"):
        assert echoed not in err


async def test_cleanup_refuses_db_echo(capsys: pytest.CaptureFixture[str]) -> None:
    assert await amain(["cleanup"], settings=Settings(db_echo=True), session_scope=_never) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_DB_ECHO + "\n"
