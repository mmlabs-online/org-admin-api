"""ADM-040: constraints, FKs and `effective_limit` of `usage_limit_overrides` (real Postgres)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.models import MAX_LIMIT, UsageMetric
from app.modules.usage.repository import UsageLimitOverrideRepository
from tests.rbac_factories import make_app, make_user

DEFAULT = 5


async def _metric(db: AsyncSession) -> UsageMetric:
    app = await make_app(db, f"ovr-{uuid.uuid4().hex[:8]}")
    metric = UsageMetric(
        app_id=app.id, key="chat-turns", display_name="Chat", unit="count", default_limit=DEFAULT
    )
    db.add(metric)
    await db.flush()
    return metric


async def _override(
    db: AsyncSession,
    user_id: uuid.UUID,
    metric_id: uuid.UUID,
    limit: int = 9,
    expires: timedelta | None = None,
    created_by: uuid.UUID | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO usage_limit_overrides (id, user_id, metric_id, limit_value, expires_at, "
            "created_by) VALUES (:id, :u, :m, :l, now() + CAST(:e AS interval), :c)"
        ),
        {
            "id": uuid.uuid4(),
            "u": user_id,
            "m": metric_id,
            "l": limit,
            "e": expires,
            "c": created_by,
        },
    )


@pytest.mark.parametrize("limit", [-1, MAX_LIMIT + 1])
async def test_limit_value_check(db_session: AsyncSession, limit: int) -> None:
    metric, user = await _metric(db_session), await make_user(db_session)
    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            await _override(db_session, user.id, metric.id, limit)
    assert "ck_usage_limit_overrides_limit_value_range" in str(exc.value)
    await _override(db_session, user.id, metric.id, 0)
    other = await make_user(db_session)
    await _override(db_session, other.id, metric.id, MAX_LIMIT)


async def test_unique_user_metric(db_session: AsyncSession) -> None:
    metric, user = await _metric(db_session), await make_user(db_session)
    await _override(db_session, user.id, metric.id)
    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            await _override(db_session, user.id, metric.id)
    assert "uq_usage_limit_overrides_user_id_metric_id" in str(exc.value)


async def _count(db: AsyncSession) -> int:
    return int(await db.scalar(text("SELECT count(*) FROM usage_limit_overrides")) or 0)


async def test_user_delete_cascades(db_session: AsyncSession) -> None:
    metric, user = await _metric(db_session), await make_user(db_session)
    await _override(db_session, user.id, metric.id)
    await db_session.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
    assert await _count(db_session) == 0


async def test_creator_delete_sets_null(db_session: AsyncSession) -> None:
    metric = await _metric(db_session)
    user, staff = await make_user(db_session), await make_user(db_session)
    await _override(db_session, user.id, metric.id, created_by=staff.id)
    await db_session.execute(text("DELETE FROM users WHERE id = :i"), {"i": staff.id})
    row = await db_session.scalar(text("SELECT created_by FROM usage_limit_overrides"))
    assert row is None
    assert await _count(db_session) == 1


async def test_metric_delete_restricted(db_session: AsyncSession) -> None:
    metric, user = await _metric(db_session), await make_user(db_session)
    await _override(db_session, user.id, metric.id)
    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM usage_metrics WHERE id = :i"), {"i": metric.id}
            )
    assert "fk_usage_limit_overrides_metric_id_usage_metrics" in str(exc.value)


async def _effective(db: AsyncSession, user_id: uuid.UUID, metric_id: uuid.UUID) -> Any:
    return await UsageLimitOverrideRepository(db).effective_limit(user_id, metric_id)


async def test_effective_limit_default_without_override(db_session: AsyncSession) -> None:
    metric, user = await _metric(db_session), await make_user(db_session)
    assert await _effective(db_session, user.id, metric.id) == DEFAULT


async def test_effective_limit_active_override(db_session: AsyncSession) -> None:
    metric, user = await _metric(db_session), await make_user(db_session)
    await _override(db_session, user.id, metric.id, 50, expires=timedelta(hours=1))
    assert await _effective(db_session, user.id, metric.id) == 50


async def test_effective_limit_expired_override_falls_back(db_session: AsyncSession) -> None:
    metric, user = await _metric(db_session), await make_user(db_session)
    await _override(db_session, user.id, metric.id, 50, expires=timedelta(minutes=-1))
    assert await _effective(db_session, user.id, metric.id) == DEFAULT


async def test_effective_limit_permanent_override(db_session: AsyncSession) -> None:
    metric, user = await _metric(db_session), await make_user(db_session)
    await _override(db_session, user.id, metric.id, 50)
    assert await _effective(db_session, user.id, metric.id) == 50


async def test_effective_limit_zero_override(db_session: AsyncSession) -> None:
    metric, user = await _metric(db_session), await make_user(db_session)
    await _override(db_session, user.id, metric.id, 0)
    assert await _effective(db_session, user.id, metric.id) == 0


async def test_effective_limit_other_user_unaffected(db_session: AsyncSession) -> None:
    metric = await _metric(db_session)
    user, other = await make_user(db_session), await make_user(db_session)
    await _override(db_session, user.id, metric.id, 50)
    assert await _effective(db_session, other.id, metric.id) == DEFAULT


async def test_effective_limit_unknown_metric_none(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    assert await _effective(db_session, user.id, uuid.uuid4()) is None
