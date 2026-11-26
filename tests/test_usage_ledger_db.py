"""ADM-042: constraints and FKs of `usage_counters` and `usage_events` (real Postgres)."""

import uuid
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.models import MAX_LIMIT
from tests.rbac_factories import make_app, make_user
from tests.usage_factories import make_metric

START = "2026-11-16 00:00:00+00"


def _times(values: dict[str, Any]) -> dict[str, Any]:
    """asyncpg wants datetimes for timestamptz parameters."""
    return {
        k: datetime.fromisoformat(v) if k in {"s", "set_at", "ref_at"} and v else v
        for k, v in values.items()
    }


class Ids:
    app_id: uuid.UUID
    user_id: uuid.UUID
    metric_id: uuid.UUID


async def _ids(db: AsyncSession) -> Ids:
    ids = Ids()
    app = await make_app(db, f"led-{uuid.uuid4().hex[:8]}")
    ids.app_id, ids.user_id = app.id, (await make_user(db)).id
    ids.metric_id = (await make_metric(db, app)).id
    return ids


async def _counter(db: AsyncSession, ids: Ids, **over: Any) -> None:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "a": ids.app_id,
        "u": ids.user_id,
        "m": ids.metric_id,
        "p": "week",
        "s": START,
        "used": 0,
        **over,
    }
    async with db.begin_nested():
        await db.execute(
            text(
                "INSERT INTO usage_counters (id, app_id, user_id, metric_id, period, "
                "period_start, used) VALUES (:id, :a, :u, :m, :p, CAST(:s AS timestamptz), :used)"
            ),
            _times(values),
        )


async def _event(db: AsyncSession, ids: Ids, **over: Any) -> None:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "a": ids.app_id,
        "u": ids.user_id,
        "m": ids.metric_id,
        "k": "key-1",
        "amount": 1,
        "state": "consumed",
        "sa": None,
        "set_at": None,
        "ref_at": None,
        **over,
    }
    async with db.begin_nested():
        await db.execute(
            text(
                "INSERT INTO usage_events (id, app_id, user_id, metric_id, idempotency_key, "
                "period, period_start, amount, settled_amount, state, settled_at, refunded_at) "
                "VALUES (:id, :a, :u, :m, :k, 'week', CAST(:s AS timestamptz), :amount, :sa, "
                ":state, CAST(:set_at AS timestamptz), CAST(:ref_at AS timestamptz))"
            ),
            _times({"s": START, **values}),
        )


async def test_used_range_check(db_session: AsyncSession) -> None:
    ids = await _ids(db_session)
    await _counter(db_session, ids, used=MAX_LIMIT)
    for bad in (-1, MAX_LIMIT + 1):
        with pytest.raises(IntegrityError, match="ck_usage_counters_used_range"):
            await _counter(db_session, ids, used=bad, s="2026-11-09 00:00:00+00")


async def test_amount_positive_check(db_session: AsyncSession) -> None:
    ids = await _ids(db_session)
    for bad in (0, -1):
        with pytest.raises(IntegrityError, match="ck_usage_events_amount_positive"):
            await _event(db_session, ids, amount=bad)
    with pytest.raises(IntegrityError, match="ck_usage_events_settled_amount_positive"):
        await _event(db_session, ids, state="settled", sa=0, set_at="2026-11-16 01:00:00+00")


@pytest.mark.parametrize("key", ["has space", "", "naïve"])
async def test_idempotency_key_format_check(db_session: AsyncSession, key: str) -> None:
    ids = await _ids(db_session)
    with pytest.raises(IntegrityError, match="ck_usage_events_idempotency_key_format"):
        await _event(db_session, ids, k=key)
    await _event(db_session, ids, k="x" * 128)


async def test_state_consistency_check(db_session: AsyncSession) -> None:
    ids = await _ids(db_session)
    ts = "2026-11-16 01:00:00+00"
    bad: list[dict[str, Any]] = [
        {"state": "settled"},  # no settled fields
        {"state": "refunded"},  # no refunded_at
        {"state": "consumed", "ref_at": ts},
        {"state": "consumed", "sa": 2, "set_at": ts},
        {"state": "settled", "sa": 2, "set_at": ts, "ref_at": ts},
        {"state": "refunded", "ref_at": ts, "sa": 2},  # settled_amount without settled_at
    ]
    for i, over in enumerate(bad):
        with pytest.raises(IntegrityError, match="ck_usage_events_state_consistent"):
            await _event(db_session, ids, k=f"bad-{i}", **over)
    await _event(db_session, ids, k="ok-1", state="settled", sa=2, set_at=ts)
    await _event(db_session, ids, k="ok-2", state="refunded", ref_at=ts)
    await _event(db_session, ids, k="ok-3", state="refunded", sa=2, set_at=ts, ref_at=ts)


async def test_unique_app_key(db_session: AsyncSession) -> None:
    ids = await _ids(db_session)
    await _event(db_session, ids)
    with pytest.raises(IntegrityError, match="uq_usage_events_app_id_idempotency_key"):
        await _event(db_session, ids)


async def test_composite_fk_rejects_other_apps_metric(db_session: AsyncSession) -> None:
    ids = await _ids(db_session)
    other = await make_app(db_session, f"led-{uuid.uuid4().hex[:8]}")
    ids.app_id = other.id  # the metric belongs to the first app
    with pytest.raises(IntegrityError, match="fk_usage_events_metric_id_app_id_usage_metrics"):
        await _event(db_session, ids)
    with pytest.raises(IntegrityError, match="fk_usage_counters_metric_id_app_id_usage_metrics"):
        await _counter(db_session, ids)


async def test_counter_unique_includes_period_kind(db_session: AsyncSession) -> None:
    ids = await _ids(db_session)
    await _counter(db_session, ids, p="week")
    await _counter(db_session, ids, p="month")  # same start, other kind: a different bucket
    with pytest.raises(IntegrityError, match="uq_usage_counters_user_id_metric_id_period"):
        await _counter(db_session, ids, p="week")
    with pytest.raises(IntegrityError, match="ck_usage_counters_period_valid"):
        await _counter(db_session, ids, p="year")


async def test_user_delete_cascades(db_session: AsyncSession) -> None:
    ids = await _ids(db_session)
    await _counter(db_session, ids)
    await _event(db_session, ids)
    await db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids.user_id})
    for table in ("usage_counters", "usage_events"):
        count = await db_session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE user_id = :u"),  # noqa: S608
            {"u": ids.user_id},
        )
        assert count == 0
