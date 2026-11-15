"""Audit retention purge CLI and service (ADM-004c): parsing, cutoff, batches, summary rows."""

import re
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.cli import AUDIT_PURGE_USAGE, MSG_AUDIT_PURGE_FAILED, MSG_DB_ECHO, amain
from app.core.config import Settings
from app.modules.audit.models import AuditLog
from app.modules.audit.purge import AuditPurgeRepository, AuditPurgeService
from app.modules.audit.service import AuditLogger
from tests.conftest import TEST_DATABASE_URL

Scope = Callable[[], AbstractAsyncContextManager[AsyncSession]]

PURGED = "audit_log.purged"
LINE = re.compile(r"^audit_log: (deleted|would delete) (\d+) \(older than (\d+) d, cutoff (\S+)\)$")


def _scope(db_session: AsyncSession) -> Scope:
    """Like the real scope: the fixture owns the transaction (commit releases a savepoint), and
    an exception rolls back the open batch, as closing the real session does."""

    @asynccontextmanager
    async def _s() -> AsyncIterator[AsyncSession]:
        try:
            yield db_session
        except Exception:
            await db_session.rollback()
            raise

    return _s


@asynccontextmanager
async def _never() -> AsyncIterator[AsyncSession]:
    raise AssertionError("session scope must not be entered")
    yield  # pragma: no cover


@asynccontextmanager
async def _failing() -> AsyncIterator[AsyncSession]:
    raise OperationalError(
        "DELETE FROM audit_log", {}, Exception("postgresql://admin:secret@db/org_admin")
    )
    yield  # pragma: no cover


def _assert_no_leak(out: str, err: str) -> None:
    both = out + err
    assert "Traceback" not in both
    assert "postgresql" not in both
    assert "secret" not in both
    assert "@" not in both


async def _aged(
    db_session: AsyncSession, days: int, action: str = "auth.login_failed"
) -> uuid.UUID:
    row_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO audit_log (id, actor_kind, action, occurred_at) VALUES (:id,"
            " 'anonymous', :action, now() - make_interval(days => :d))"
        ),
        {"id": row_id, "action": action, "d": days},
    )
    return row_id


async def _ids(db_session: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    return set((await db_session.scalars(select(AuditLog.id).where(AuditLog.id.in_(ids)))).all())


async def _summaries(db_session: AsyncSession) -> list[AuditLog]:
    # Old seeded `audit_log.purged` rows are excluded: summaries are written now().
    rows = await db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == PURGED, AuditLog.occurred_at >= func.now() - text("interval '1 day'")
        )
    )
    return list(rows)


async def _flag(db_session: AsyncSession) -> Any:
    return await db_session.scalar(text("SELECT current_setting('audit.allow_purge', true)"))


async def _run(db_session: AsyncSession, *argv: str) -> int:
    return await amain(
        ["audit-purge", *argv], settings=Settings(), session_scope=_scope(db_session)
    )


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--dry-run"],
        ["--older-than-days", "89"],
        ["--older-than-days", "0"],
        ["--older-than-days", "-5"],
        ["--older-than-days", "3651"],
        ["--older-than-days", "90.5"],
        ["--older-than-days", "abc"],
        ["--older-than-days", "٩٠"],
        ["--older-than-days", " 365"],
        ["--older-than-days", "365", "--bogus"],
        ["--older", "365"],
        ["--older-than-days", "365", "--help"],
    ],
)
async def test_usage_errors(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    assert await amain(["audit-purge", *argv], settings=Settings(), session_scope=_never) == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == AUDIT_PURGE_USAGE + "\n"  # a fixed line: the bad value is never echoed


async def test_repository_rechecks_min_days(db_session: AsyncSession) -> None:
    repo = AuditPurgeRepository(db_session)
    with pytest.raises(ValueError, match="minimum"):
        await repo.cutoff(89)
    with pytest.raises(ValueError, match="minimum"):
        await repo.cutoff(True)
    assert await repo.cutoff(90) is not None


async def test_purge_deletes_only_older_than_cutoff(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = [await _aged(db_session, d) for d in (400, 366, 364, 0)]
    assert await _run(db_session, "--older-than-days", "365") == 0
    out, err = capsys.readouterr()
    assert err == ""
    _assert_no_leak(out, err)
    (line,) = out.splitlines()
    match = LINE.match(line)
    assert match is not None
    assert match.group(1, 2, 3) == ("deleted", "2", "365")
    assert await _ids(db_session, ids) == {ids[2], ids[3]}


async def test_purge_keeps_purge_trail(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    trail = await _aged(db_session, 400, action=PURGED)
    assert await _run(db_session, "--older-than-days", "365", "--dry-run") == 0
    match = LINE.match(capsys.readouterr().out.splitlines()[0])
    assert match is not None and match.group(1, 2) == ("would delete", "0")
    assert await _run(db_session, "--older-than-days", "365") == 0
    assert await _ids(db_session, [trail]) == {trail}


async def test_purge_batches_share_cutoff(db_session: AsyncSession) -> None:
    ids = [await _aged(db_session, 400) for _ in range(5)]
    result = await AuditPurgeService(db_session, batch_size=2).run(365, dry_run=False)
    assert (result.older_than_days, result.deleted) == (365, 5)
    assert await _ids(db_session, ids) == set()
    rows = await _summaries(db_session)
    assert sorted(r.changes["deleted"] for r in rows if r.changes) == [1, 2, 2]
    assert {r.changes["cutoff"] for r in rows if r.changes} == {result.cutoff.isoformat()}


async def test_purge_summary_row_shape(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _aged(db_session, 400)
    await _aged(db_session, 370)
    assert await _run(db_session, "--older-than-days", "365") == 0
    match = LINE.match(capsys.readouterr().out.strip())
    assert match is not None
    (row,) = await _summaries(db_session)
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("system", None, "system:cli")
    assert (row.target_type, row.target_id, row.app_key, row.org_id) == (None, None, None, None)
    assert (row.ip, row.user_agent, row.request_id, row.outcome) == (None, None, None, "success")
    assert row.changes == {"cutoff": match.group(4), "older_than_days": 365, "deleted": 2}


async def test_purge_zero_rows_writes_nothing(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _aged(db_session, 100)
    assert await _run(db_session, "--older-than-days", "365") == 0
    match = LINE.match(capsys.readouterr().out.strip())
    assert match is not None and match.group(1, 2) == ("deleted", "0")
    assert await _summaries(db_session) == []


async def test_purge_summary_failure_rolls_back_batch(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = [await _aged(db_session, 400) for _ in range(2)]

    async def boom(self: AuditLogger, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("summary insert failed")

    monkeypatch.setattr(AuditLogger, "record", boom)
    assert await _run(db_session, "--older-than-days", "365") == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_AUDIT_PURGE_FAILED + "\n"
    assert await _ids(db_session, ids) == set(ids)
    assert await _summaries(db_session) == []


async def test_dry_run_changes_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `SET TRANSACTION READ ONLY` cannot run inside the fixture's savepoint: proven separately.
    async def no_op(self: AuditPurgeRepository) -> None:
        return None

    monkeypatch.setattr(AuditPurgeRepository, "start_read_only", no_op)
    ids = [await _aged(db_session, d) for d in (400, 366, 10)]
    assert await _run(db_session, "--older-than-days", "365", "--dry-run") == 0
    out, err = capsys.readouterr()
    assert err == ""
    first, second = out.splitlines()
    match = LINE.match(first)
    assert match is not None and match.group(1, 2, 3) == ("would delete", "2", "365")
    assert second == "dry-run: nothing was deleted"
    assert await _ids(db_session, ids) == set(ids)
    assert await _summaries(db_session) == []
    assert await _flag(db_session) != "on"


async def test_dry_run_transaction_is_read_only() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with AsyncSession(engine) as session:
            await AuditPurgeRepository(session).start_read_only()
            with pytest.raises(DBAPIError, match="read-only transaction"):
                await session.execute(
                    text(
                        "INSERT INTO audit_log (id, actor_kind, action) "
                        "VALUES (gen_random_uuid(), 'anonymous', 'auth.login_failed')"
                    )
                )
            await session.rollback()
    finally:
        await engine.dispose()


async def test_flag_reset_after_run(db_session: AsyncSession) -> None:
    await _aged(db_session, 400)
    assert await _run(db_session, "--older-than-days", "365") == 0
    assert await _flag(db_session) == "off"
    row_id = await _aged(db_session, 91)
    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        async with db_session.begin_nested():
            await db_session.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": row_id})


async def test_db_failure_fixed_line(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["audit-purge", "--older-than-days", "365"]
    assert await amain(argv, settings=Settings(), session_scope=_failing) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_AUDIT_PURGE_FAILED + "\n"
    _assert_no_leak(out, err)


async def test_refuses_db_echo(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["audit-purge", "--older-than-days", "365"]
    assert await amain(argv, settings=Settings(db_echo=True), session_scope=_never) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_DB_ECHO + "\n"
