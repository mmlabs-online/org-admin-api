"""Retention of decided join requests (USR-15): what is purged, the cooldown guard, batches."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.auth.retention import PurgeCount
from app.modules.join_requests.models import AppJoinRequest
from app.modules.join_requests.repository import JoinRequestRetentionRepository
from app.modules.join_requests.retention import JoinRequestRetentionService
from app.modules.rbac.models import App
from tests.auth_factories import make_login_user
from tests.join_request_factories import make_approval_app, make_join_request
from tests.purge_plan import force_rescanned_semijoin

RETENTION = timedelta(days=180)
COOLDOWN = 604800


async def _row(db: AsyncSession, app: App, status: str, age: timedelta | None) -> AppJoinRequest:
    """A request whose `decided_at` (pending: `created_at`) is `age` old."""
    row = await make_join_request(db, app, await make_login_user(db), status)
    column = AppJoinRequest.created_at if status == "pending" else AppJoinRequest.decided_at
    if age is not None:
        await db.execute(
            update(AppJoinRequest)
            .where(AppJoinRequest.id == row.id)
            .values({column.key: func.now() - age})
        )
    return row


async def _ids(db: AsyncSession) -> set[object]:
    return set((await db.scalars(select(AppJoinRequest.id))).all())


def _service(db: AsyncSession, **kwargs: int | timedelta) -> JoinRequestRetentionService:
    args: dict[str, int | timedelta] = {"retention": RETENTION, "cooldown_seconds": COOLDOWN}
    args.update(kwargs)
    return JoinRequestRetentionService(db, **args)  # type: ignore[arg-type]


async def test_purges_old_decided_rows(db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    old = timedelta(days=181)
    gone = [await _row(db_session, app, s, old) for s in ("approved", "cancelled", "rejected")]
    (result,) = await _service(db_session).run(dry_run=False)
    assert result == PurgeCount("join_requests", 3, "decided, older than 180 d")
    assert not ({r.id for r in gone} & await _ids(db_session))


async def test_keeps_recent_decided_rows(db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    row = await _row(db_session, app, "approved", timedelta(days=10))
    (result,) = await _service(db_session).run(dry_run=False)
    assert result.rows == 0
    assert row.id in await _ids(db_session)


async def test_keeps_rejection_inside_cooldown(db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    rejected = await _row(db_session, app, "rejected", timedelta(days=3))
    approved = await _row(db_session, app, "approved", timedelta(days=3))
    service = _service(db_session, retention=timedelta(days=1))  # shorter than the cooldown
    (result,) = await service.run(dry_run=False)
    assert result.rows == 1
    assert await _ids(db_session) == {rejected.id}
    assert approved.id not in await _ids(db_session)


async def test_never_purges_pending(db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    row = await _row(db_session, app, "pending", timedelta(days=730))
    (result,) = await _service(db_session).run(dry_run=False)
    assert result.rows == 0
    assert row.id in await _ids(db_session)


async def test_cooldown_zero_keeps_nothing_extra(db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    row = await _row(db_session, app, "rejected", timedelta(days=3))
    (result,) = await _service(db_session, retention=timedelta(days=1), cooldown_seconds=0).run(
        dry_run=False
    )
    assert result.rows == 1
    assert row.id not in await _ids(db_session)


async def _purge_audit(db: AsyncSession) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.action == "join_request_retention.purged")
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_batches_write_one_summary_each(db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    for _ in range(5):
        await _row(db_session, app, "approved", timedelta(days=200))
    (result,) = await JoinRequestRetentionService(
        db_session, retention=RETENTION, cooldown_seconds=COOLDOWN, batch_size=2
    ).run(dry_run=False)
    assert result.rows == 5
    rows = await _purge_audit(db_session)
    assert sorted(r.changes["join_requests"]["to"] for r in rows) == [1, 2, 2]
    for r in rows:
        assert r.changes["join_requests"]["from"] is None
        assert (r.actor_kind, r.actor_label) == ("system", "system:cli")


async def test_dry_run_counts_only(db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    for _ in range(2):
        await _row(db_session, app, "approved", timedelta(days=200))
    before = await _ids(db_session)
    (result,) = await _service(db_session).run(dry_run=True)
    assert result.rows == 2
    assert await _ids(db_session) == before
    assert await _purge_audit(db_session) == []


async def test_retention_floor(db_session: AsyncSession) -> None:
    repo = JoinRequestRetentionRepository(db_session)
    with pytest.raises(ValueError, match="minimum"):
        await repo.count(timedelta(hours=23), 0)
    with pytest.raises(ValueError, match="minimum"):
        await repo.purge(timedelta(hours=23), 0, 10)


async def test_purge_respects_limit_when_subquery_rescanned(db_session: AsyncSession) -> None:
    """BUG-001: a rescanned `LIMIT ... FOR UPDATE` subquery deleted more than one batch."""
    app = await make_approval_app(db_session)
    for days in (205, 204, 203, 202, 201):  # oldest first
        await _row(db_session, app, "approved", timedelta(days=days))
    await force_rescanned_semijoin(db_session)
    repo = JoinRequestRetentionRepository(db_session)
    assert await repo.purge(RETENTION, COOLDOWN, 2) == 2
    assert len(await _ids(db_session)) == 3
