"""ADM-045: usage event retention purge (service and repository)."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.session import SessionLocal
from app.modules.audit.models import AuditLog
from app.modules.usage.models import UsageCounter, UsageEvent
from app.modules.usage.retention import (
    UsageEventRetentionRepository,
    UsageEventRetentionService,
)
from tests.conftest import TEST_DATABASE_URL
from tests.purge_plan import force_rescanned_semijoin
from tests.usage_factories import World, consume, counters, world

ACTION = "usage_events.purged"


async def _event(
    db: AsyncSession, w: World, days: int, state: str = "consumed", key: str | None = None
) -> uuid.UUID:
    row_id = uuid.uuid4()
    settled = state == "settled"
    refunded = state == "refunded"
    await db.execute(
        text(
            "INSERT INTO usage_events (id, app_id, user_id, metric_id, idempotency_key, period,"
            " period_start, amount, state, created_at, settled_amount, settled_at, refunded_at)"
            " VALUES (:id, :app, :user, :metric, :key, 'week', now(), 1, :state,"
            " now() - make_interval(days => :d),"
            " CASE WHEN :settled THEN 1 END, CASE WHEN :settled THEN now() END,"
            " CASE WHEN :refunded THEN now() END)"
        ),
        {
            "id": row_id,
            "app": w.app.id,
            "user": w.user.id,
            "metric": w.metric.id,
            "key": key or f"k-{row_id.hex}",
            "state": state,
            "d": days,
            "settled": settled,
            "refunded": refunded,
        },
    )
    return row_id


async def _ids(db: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    rows = await db.scalars(select(UsageEvent.id).where(UsageEvent.id.in_(ids)))
    return set(rows.all())


async def _summaries(db: AsyncSession) -> list[AuditLog]:
    # Only rows of this transaction: committed tests leave their append-only summaries behind.
    rows = await db.scalars(
        select(AuditLog).where(AuditLog.action == ACTION, AuditLog.occurred_at >= func.now())
    )
    return list(rows)


async def test_purges_events_older_than_retention(db_session: AsyncSession) -> None:
    w = await world(db_session)
    ids = [await _event(db_session, w, 91, s) for s in ("consumed", "settled", "refunded")]
    result = await UsageEventRetentionService(db_session).run(90, apply=True)
    assert result.deleted == 3
    assert await _ids(db_session, ids) == set()


async def test_keeps_recent_events(db_session: AsyncSession) -> None:
    w = await world(db_session)
    ids = [await _event(db_session, w, d) for d in (89, 0)]
    await UsageEventRetentionService(db_session).run(90, apply=True)
    assert await _ids(db_session, ids) == set(ids)


async def test_counters_untouched(db_session: AsyncSession) -> None:
    w = await world(db_session)
    await _event(db_session, w, 120)
    db_session.add(
        UsageCounter(
            app_id=w.app.id,
            user_id=w.user.id,
            metric_id=w.metric.id,
            period="week",
            period_start=datetime(2026, 1, 5, tzinfo=UTC),
            used=7,
        )
    )
    await db_session.flush()
    before = [(c.id, c.used, c.updated_at) for c in await counters(db_session, w.user.id)]
    await UsageEventRetentionService(db_session).run(90, apply=True)
    after = [(c.id, c.used, c.updated_at) for c in await counters(db_session, w.user.id)]
    assert before == after and len(after) == 1


async def test_dry_run_changes_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `SET TRANSACTION READ ONLY` cannot run inside the fixture's savepoint: proven separately.
    async def no_op(self: UsageEventRetentionRepository) -> None:
        return None

    monkeypatch.setattr(UsageEventRetentionRepository, "start_read_only", no_op)
    w = await world(db_session)
    ids = [await _event(db_session, w, d) for d in (100, 95, 10)]
    result = await UsageEventRetentionService(db_session).run(90, apply=False)
    assert result.deleted == 2
    assert await _ids(db_session, ids) == set(ids)
    assert await _summaries(db_session) == []


async def test_dry_run_transaction_is_read_only() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with AsyncSession(engine) as session:
            await UsageEventRetentionRepository(session).start_read_only()
            with pytest.raises(DBAPIError, match="read-only transaction"):
                await session.execute(text("DELETE FROM usage_events WHERE false"))
            await session.rollback()
    finally:
        await engine.dispose()


async def test_runs_several_batches(db_session: AsyncSession) -> None:
    w = await world(db_session)
    ids = [await _event(db_session, w, 100) for _ in range(5)]
    result = await UsageEventRetentionService(db_session, batch_size=2).run(90, apply=True)
    assert result.deleted == 5
    assert await _ids(db_session, ids) == set()
    rows = await _summaries(db_session)
    assert sorted(r.changes["deleted"] for r in rows if r.changes) == [1, 2, 2]
    assert {r.changes["cutoff"] for r in rows if r.changes} == {result.cutoff.isoformat()}


async def test_zero_rows_writes_no_summary(db_session: AsyncSession) -> None:
    w = await world(db_session)
    await _event(db_session, w, 10)
    result = await UsageEventRetentionService(db_session).run(90, apply=True)
    assert result.deleted == 0
    assert await _summaries(db_session) == []


async def test_summary_row_shape(db_session: AsyncSession) -> None:
    w = await world(db_session)
    await _event(db_session, w, 100)
    await _event(db_session, w, 101)
    result = await UsageEventRetentionService(db_session).run(90, apply=True)
    (row,) = await _summaries(db_session)
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("system", None, "system:cli")
    assert (row.target_type, row.target_id, row.app_key, row.org_id) == (None, None, None, None)
    assert row.changes == {
        "cutoff": result.cutoff.isoformat(),
        "retention_days": 90,
        "deleted": 2,
    }


async def test_repository_refuses_below_floor(db_session: AsyncSession) -> None:
    repo = UsageEventRetentionRepository(db_session)
    with pytest.raises(ValueError, match="minimum"):
        await repo.cutoff(89)
    with pytest.raises(ValueError, match="minimum"):
        await repo.cutoff(True)
    assert await repo.cutoff(90) is not None


async def test_delete_batch_respects_limit_when_subquery_rescanned(
    db_session: AsyncSession,
) -> None:
    w = await world(db_session)
    ids = [await _event(db_session, w, 100 + i) for i in range(5)]
    repo = UsageEventRetentionRepository(db_session)
    cutoff = await repo.cutoff(90)
    await force_rescanned_semijoin(db_session)
    assert await repo.delete_batch(cutoff, 2) == 2
    assert len(await _ids(db_session, ids)) == 3


async def test_skips_locked_rows() -> None:
    """Committed data, cleaned up by id (the append-only summary stays); another holds a row."""
    async with SessionLocal() as s:
        w = await world(s)
        ids = [await _event(s, w, 100) for _ in range(3)]
        await s.commit()
    try:
        async with SessionLocal() as holder:
            await holder.execute(
                select(UsageEvent.id).where(UsageEvent.id == ids[0]).with_for_update()
            )
            async with SessionLocal() as s:
                result = await asyncio.wait_for(
                    UsageEventRetentionService(s).run(90, apply=True), timeout=15
                )
            assert result.deleted >= 2
            await holder.rollback()
        async with SessionLocal() as s:
            assert await _ids(s, ids) == {ids[0]}
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(UsageEvent).where(UsageEvent.app_id == w.app.id))
            await _drop_world(s, w)
            await s.commit()


async def _drop_world(s: AsyncSession, w: World) -> None:
    from app.modules.rbac.models import App, Role, RoleAssignment
    from app.modules.usage.models import UsageMetric
    from app.modules.users.models import User

    await s.execute(delete(UsageCounter).where(UsageCounter.app_id == w.app.id))
    await s.execute(delete(UsageMetric).where(UsageMetric.id == w.metric.id))
    await s.execute(delete(RoleAssignment).where(RoleAssignment.user_id == w.user.id))
    await s.execute(delete(Role).where(Role.id == w.role.id))
    await s.execute(delete(User).where(User.id == w.user.id))
    await s.execute(text("DELETE FROM app_credentials WHERE app_id = :a"), {"a": w.app.id})
    await s.execute(delete(App).where(App.id == w.app.id))


async def test_purged_key_consumes_again(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=5)
    first = await consume(client, w, "again", 1)
    assert first.json()["replayed"] is False
    await db_session.execute(
        text("UPDATE usage_events SET created_at = now() - interval '100 days'")
    )
    await UsageEventRetentionService(db_session).run(90, apply=True)
    second = await consume(client, w, "again", 1)
    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is False
    assert second.json()["used"] == 2
