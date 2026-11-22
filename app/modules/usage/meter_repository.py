"""SQL of the app-facing usage routes (ADM-042). Raw SQL only where one statement must decide
atomically; everything else is Core/ORM. No `lock_app`, no `FOR UPDATE` on metric or app rows
(ADR-0022 §3)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, case, delete, func, literal, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.models import (
    MAX_LIMIT,
    UsageCounter,
    UsageEvent,
    UsageLimitOverride,
    UsageMetric,
    UsageMetricStatus,
)

LOCK_TIMEOUT = "2s"  # a stuck row lock becomes an error, never a long wait

# The user's limit: an unexpired override, else the metric default (same rule as
# `UsageLimitOverrideRepository.effective_limit`; expiry on the DB clock).
_LIMIT_SQL = """COALESCE(
    (SELECT o.limit_value FROM usage_limit_overrides o
     WHERE o.user_id = CAST(:user_id AS uuid) AND o.metric_id = CAST(:metric_id AS uuid)
       AND (o.expires_at IS NULL OR o.expires_at > now())),
    (SELECT m.default_limit FROM usage_metrics m WHERE m.id = CAST(:metric_id AS uuid))
)"""

_CONSUME_SQL = text(
    f"""
INSERT INTO usage_counters (id, app_id, user_id, metric_id, period, period_start, used)
SELECT gen_random_uuid(), CAST(:app_id AS uuid), CAST(:user_id AS uuid),
       CAST(:metric_id AS uuid), CAST(:period AS varchar), CAST(:period_start AS timestamptz),
       CAST(:amount AS bigint)
WHERE CAST(:amount AS bigint) <= {_LIMIT_SQL}
ON CONFLICT (user_id, metric_id, period, period_start)
DO UPDATE SET used = usage_counters.used + EXCLUDED.used, updated_at = now()
WHERE usage_counters.used + EXCLUDED.used <= {_LIMIT_SQL}
RETURNING used, {_LIMIT_SQL} AS "limit"
"""
)

_ADJUST_SQL = text(
    f"""
UPDATE usage_counters SET used = GREATEST(0, used + CAST(:delta AS bigint)), updated_at = now()
WHERE user_id = CAST(:user_id AS uuid) AND metric_id = CAST(:metric_id AS uuid)
  AND period = CAST(:period AS varchar) AND period_start = CAST(:period_start AS timestamptz)
  AND used + CAST(:delta AS bigint) <= {MAX_LIMIT}
RETURNING used
"""  # noqa: S608 (constant fragment, values are bound)
)


@dataclass(frozen=True)
class MetricUsageRow:
    key: str
    display_name: str
    unit: str
    period: str
    limit: int
    used: int


class UsageMeterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def db_now(self) -> datetime:
        now: datetime | None = await self.session.scalar(select(func.now()))
        assert now is not None  # noqa: S101
        return now

    async def set_lock_timeout(self) -> None:
        """Transaction-local; reverts at commit or rollback."""
        await self.session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))

    async def find_metric(self, app_id: uuid.UUID, key: str) -> UsageMetric | None:
        """The app's metric by key, any status (callers decide about archived ones)."""
        return await self.session.scalar(
            select(UsageMetric)
            .where(UsageMetric.app_id == app_id, UsageMetric.key == key)
            .execution_options(populate_existing=True)
        )

    async def metric_key(self, metric_id: uuid.UUID) -> str | None:
        return await self.session.scalar(select(UsageMetric.key).where(UsageMetric.id == metric_id))

    async def insert_event(
        self,
        *,
        app_id: uuid.UUID,
        user_id: uuid.UUID,
        metric_id: uuid.UUID,
        idempotency_key: str,
        period: str,
        period_start: datetime,
        amount: int,
    ) -> UsageEvent | None:
        """None when the (app, idempotency_key) record already exists."""
        return await self.session.scalar(
            pg_insert(UsageEvent)
            .values(
                id=uuid.uuid4(),
                app_id=app_id,
                user_id=user_id,
                metric_id=metric_id,
                idempotency_key=idempotency_key,
                period=period,
                period_start=period_start,
                amount=amount,
            )
            .on_conflict_do_nothing(constraint="uq_usage_events_app_id_idempotency_key")
            .returning(UsageEvent)
            .execution_options(populate_existing=True)
        )

    async def discard_event(self, event_id: uuid.UUID) -> None:
        """Remove the event of a refused consume: refusals are not stored."""
        await self.session.execute(
            delete(UsageEvent)
            .where(UsageEvent.id == event_id)
            .execution_options(synchronize_session=False)
        )

    async def get_event(
        self, app_id: uuid.UUID, idempotency_key: str, *, for_update: bool
    ) -> UsageEvent | None:
        stmt = (
            select(UsageEvent)
            .where(UsageEvent.app_id == app_id, UsageEvent.idempotency_key == idempotency_key)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def save_event(self) -> None:
        await self.session.flush()

    async def consume_counter(
        self,
        *,
        app_id: uuid.UUID,
        user_id: uuid.UUID,
        metric_id: uuid.UUID,
        period: str,
        period_start: datetime,
        amount: int,
    ) -> tuple[int, int] | None:
        """Charge `amount` only if it fits the limit, in one statement (concurrent-safe).
        Returns (used, limit), or None when over the limit (nothing changed)."""
        row = (
            await self.session.execute(
                _CONSUME_SQL,
                {
                    "app_id": app_id,
                    "user_id": user_id,
                    "metric_id": metric_id,
                    "period": period,
                    "period_start": period_start,
                    "amount": amount,
                },
            )
        ).one_or_none()
        return None if row is None else (row[0], row[1])

    async def adjust_counter(
        self,
        *,
        user_id: uuid.UUID,
        metric_id: uuid.UUID,
        period: str,
        period_start: datetime,
        delta: int,
    ) -> bool:
        """`used = max(0, used + delta)`; False when the result would exceed `MAX_LIMIT`."""
        row = (
            await self.session.execute(
                _ADJUST_SQL,
                {
                    "user_id": user_id,
                    "metric_id": metric_id,
                    "period": period,
                    "period_start": period_start,
                    "delta": delta,
                },
            )
        ).one_or_none()
        return row is not None

    async def snapshot(
        self,
        *,
        user_id: uuid.UUID,
        metric_id: uuid.UUID,
        period: str,
        period_start: datetime,
    ) -> tuple[int, int]:
        """Live (used, limit) of one counter."""
        used = await self.session.scalar(
            select(UsageCounter.used).where(
                UsageCounter.user_id == user_id,
                UsageCounter.metric_id == metric_id,
                UsageCounter.period == period,
                UsageCounter.period_start == period_start,
            )
        )
        limit = await self.session.scalar(
            text(f"SELECT {_LIMIT_SQL}"),
            {"user_id": user_id, "metric_id": metric_id},
        )
        assert limit is not None  # noqa: S101 (the metric exists: FK)
        return used or 0, limit

    async def read_user_usage(
        self,
        user_id: uuid.UUID,
        app_id: uuid.UUID,
        starts: dict[str, datetime],
        metric_key: str | None,
    ) -> list[MetricUsageRow]:
        """Active metrics of the app with the user's limit and used, in one query."""
        o, m, c = UsageLimitOverride, UsageMetric, UsageCounter
        start = case(
            {k: literal(v, DateTime(timezone=True)) for k, v in starts.items()}, value=m.period
        )
        stmt = (
            select(
                m.key,
                m.display_name,
                m.unit,
                m.period,
                func.coalesce(o.limit_value, m.default_limit),
                func.coalesce(c.used, 0),
            )
            .select_from(m)
            .outerjoin(
                o,
                (o.metric_id == m.id)
                & (o.user_id == user_id)
                & ((o.expires_at.is_(None)) | (o.expires_at > func.now())),
            )
            .outerjoin(
                c,
                (c.metric_id == m.id)
                & (c.user_id == user_id)
                & (c.period == m.period)
                & (c.period_start == start),
            )
            .where(m.app_id == app_id, m.status == UsageMetricStatus.ACTIVE.value)
            .order_by(m.key)
        )
        if metric_key is not None:
            stmt = stmt.where(m.key == metric_key)
        return [MetricUsageRow(*r) for r in await self.session.execute(stmt)]
