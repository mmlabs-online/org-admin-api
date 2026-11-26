"""ADM-045: `usage-events-purge` CLI: parsing, dry-run default, output, failures."""

import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import (
    MSG_DB_ECHO,
    MSG_USAGE_EVENTS_PURGE_FAILED,
    USAGE_EVENTS_PURGE_USAGE,
    amain,
)
from app.core.config import Settings
from app.modules.audit.models import AuditLog
from app.modules.usage.retention import UsageEventRetentionRepository
from tests.test_usage_events_retention import _event, _ids
from tests.usage_factories import world

Scope = Callable[[], AbstractAsyncContextManager[AsyncSession]]

LINE = re.compile(
    r"^usage_events: (deleted|would delete) (\d+) \(older than (\d+) d, cutoff (\S+)\)$"
)


def _scope(db_session: AsyncSession) -> Scope:
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
        "DELETE FROM usage_events", {}, Exception("postgresql://admin:secret@db/org_admin")
    )
    yield  # pragma: no cover


async def _run(db_session: AsyncSession, *argv: str) -> int:
    return await amain(
        ["usage-events-purge", *argv], settings=Settings(), session_scope=_scope(db_session)
    )


@pytest.mark.parametrize(
    "argv", [["--dry-run"], ["--bogus"], ["--app", "x"], ["--help"], ["--apply", "x"], ["--app"]]
)
async def test_usage_errors(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    code = await amain(["usage-events-purge", *argv], settings=Settings(), session_scope=_never)
    assert code == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == USAGE_EVENTS_PURGE_USAGE + "\n"


async def test_default_is_dry_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_op(self: UsageEventRetentionRepository) -> None:
        return None

    monkeypatch.setattr(UsageEventRetentionRepository, "start_read_only", no_op)
    w = await world(db_session)
    ids = [await _event(db_session, w, d) for d in (100, 10)]
    assert await _run(db_session) == 0
    out, err = capsys.readouterr()
    assert err == ""
    first, second = out.splitlines()
    match = LINE.match(first)
    assert match is not None and match.group(1, 2, 3) == ("would delete", "1", "90")
    assert second == "dry-run: nothing was deleted"
    assert await _ids(db_session, ids) == set(ids)
    rows = await db_session.scalars(
        select(AuditLog).where(AuditLog.action == "usage_events.purged")
    )
    assert list(rows) == []


async def test_apply_deletes(db_session: AsyncSession, capsys: pytest.CaptureFixture[str]) -> None:
    w = await world(db_session)
    ids = [await _event(db_session, w, d) for d in (100, 10)]
    assert await _run(db_session, "--apply") == 0
    out, err = capsys.readouterr()
    assert err == ""
    match = LINE.match(out.strip())
    assert match is not None and match.group(1, 2, 3) == ("deleted", "1", "90")
    assert await _ids(db_session, ids) == {ids[1]}


async def test_output_counts_only(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    w = await world(db_session)
    await _event(db_session, w, 100, key="secret-idem-key")
    assert await _run(db_session, "--apply") == 0
    out, err = capsys.readouterr()
    both = out + err
    assert "secret-idem-key" not in both
    assert str(w.user.id) not in both
    assert "@" not in both and "Traceback" not in both


async def test_db_failure_fixed_line(capsys: pytest.CaptureFixture[str]) -> None:
    code = await amain(
        ["usage-events-purge", "--apply"], settings=Settings(), session_scope=_failing
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_USAGE_EVENTS_PURGE_FAILED + "\n"
    assert "postgresql" not in err and "secret" not in err


async def test_refuses_db_echo(capsys: pytest.CaptureFixture[str]) -> None:
    code = await amain(
        ["usage-events-purge"], settings=Settings(db_echo=True), session_scope=_never
    )
    assert code == 1
    assert capsys.readouterr().err == MSG_DB_ECHO + "\n"
