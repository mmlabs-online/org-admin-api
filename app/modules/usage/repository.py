import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import bumped_updated_at
from app.modules.usage.models import UsageLimitOverride, UsageMetric


@dataclass(frozen=True)
class UsageMetricView:
    """Plain values for the router, so it never touches the ORM."""

    id: uuid.UUID
    app_key: str
    key: str
    display_name: str
    unit: str
    default_limit: int
    period: str
    status: str
    created_at: datetime
    updated_at: datetime


def view(metric: UsageMetric, app_key: str) -> UsageMetricView:
    return UsageMetricView(
        id=metric.id,
        app_key=app_key,
        key=metric.key,
        display_name=metric.display_name,
        unit=metric.unit,
        default_limit=metric.default_limit,
        period=metric.period,
        status=metric.status,
        created_at=metric.created_at,
        updated_at=metric.updated_at,
    )


class UsageMetricRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(
        self, app_id: uuid.UUID, *, status: str | None, limit: int, offset: int
    ) -> tuple[list[UsageMetric], int]:
        where = [UsageMetric.app_id == app_id]
        if status is not None:
            where.append(UsageMetric.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(UsageMetric).where(*where)
        )
        result = await self.session.scalars(
            select(UsageMetric)
            .where(*where)
            .order_by(UsageMetric.key, UsageMetric.id)
            .limit(limit)
            .offset(offset)
            .execution_options(populate_existing=True)
        )
        return list(result), total or 0

    async def get(self, app_id: uuid.UUID, metric_id: uuid.UUID) -> UsageMetric | None:
        return await self.session.scalar(
            select(UsageMetric)
            .where(UsageMetric.app_id == app_id, UsageMetric.id == metric_id)
            .execution_options(populate_existing=True)
        )

    async def get_for_update(self, app_id: uuid.UUID, metric_id: uuid.UUID) -> UsageMetric | None:
        return await self.session.scalar(
            select(UsageMetric)
            .where(UsageMetric.app_id == app_id, UsageMetric.id == metric_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )

    async def find_by_key(self, app_id: uuid.UUID, key: str) -> UsageMetric | None:
        return await self.session.scalar(
            select(UsageMetric).where(UsageMetric.app_id == app_id, UsageMetric.key == key)
        )

    async def add(self, metric: UsageMetric) -> None:
        self.session.add(metric)
        await self.session.flush()
        await self.session.refresh(metric)  # server defaults

    async def apply(self, metric_id: uuid.UUID, values: dict[str, Any]) -> None:
        await self.session.execute(
            update(UsageMetric)
            .where(UsageMetric.id == metric_id)
            .values(**values, updated_at=bumped_updated_at(UsageMetric.updated_at))
            .execution_options(synchronize_session=False)
        )


@dataclass(frozen=True)
class UsageLimitOverrideView:
    app_key: str
    metric_id: uuid.UUID
    user_id: uuid.UUID
    limit: int
    expires_at: datetime | None
    active: bool
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


_O = UsageLimitOverride
_ACTIVE = or_(_O.expires_at.is_(None), _O.expires_at > func.now())


class UsageLimitOverrideRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def db_now(self) -> datetime:
        now: datetime | None = await self.session.scalar(select(func.now()))
        assert now is not None  # noqa: S101
        return now

    @staticmethod
    def _view(app_key: str, o: UsageLimitOverride, active: bool) -> UsageLimitOverrideView:
        return UsageLimitOverrideView(
            app_key=app_key,
            metric_id=o.metric_id,
            user_id=o.user_id,
            limit=o.limit_value,
            expires_at=o.expires_at,
            active=active,
            created_by=o.created_by,
            created_at=o.created_at,
            updated_at=o.updated_at,
        )

    async def page(
        self, app_key: str, metric_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[UsageLimitOverrideView], int]:
        total = await self.session.scalar(
            select(func.count()).select_from(_O).where(_O.metric_id == metric_id)
        )
        rows = await self.session.execute(
            select(_O, _ACTIVE.label("active"))
            .where(_O.metric_id == metric_id)
            .order_by(_O.created_at, _O.id)
            .limit(limit)
            .offset(offset)
            .execution_options(populate_existing=True)
        )
        return [self._view(app_key, o, bool(a)) for o, a in rows], total or 0

    async def get_view(
        self, app_key: str, user_id: uuid.UUID, metric_id: uuid.UUID
    ) -> UsageLimitOverrideView | None:
        row = (
            await self.session.execute(
                select(_O, _ACTIVE.label("active"))
                .where(_O.user_id == user_id, _O.metric_id == metric_id)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        return None if row is None else self._view(app_key, row[0], bool(row[1]))

    async def get_for_update(
        self, user_id: uuid.UUID, metric_id: uuid.UUID
    ) -> UsageLimitOverride | None:
        return await self.session.scalar(
            select(_O)
            .where(_O.user_id == user_id, _O.metric_id == metric_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        metric_id: uuid.UUID,
        limit_value: int,
        expires_at: datetime | None,
        created_by: uuid.UUID | None,
    ) -> UsageLimitOverride | None:
        """None when a concurrent insert for the same (user, metric) won."""
        return await self.session.scalar(
            pg_insert(_O)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                metric_id=metric_id,
                limit_value=limit_value,
                expires_at=expires_at,
                created_by=created_by,
            )
            .on_conflict_do_nothing(constraint="uq_usage_limit_overrides_user_id_metric_id")
            .returning(_O)
            .execution_options(populate_existing=True)
        )

    async def apply(self, override_id: uuid.UUID, values: dict[str, Any]) -> None:
        await self.session.execute(
            update(_O)
            .where(_O.id == override_id)
            .values(**values, updated_at=bumped_updated_at(_O.updated_at))
            .execution_options(synchronize_session=False)
        )

    async def delete(self, user_id: uuid.UUID, metric_id: uuid.UUID) -> UsageLimitOverride | None:
        """Delete and return the old row (atomic old values for the audit entry)."""
        return await self.session.scalar(
            delete(_O)
            .where(_O.user_id == user_id, _O.metric_id == metric_id)
            .returning(_O)
            .execution_options(populate_existing=True)
        )

    async def effective_limit(self, user_id: uuid.UUID, metric_id: uuid.UUID) -> int | None:
        """The user's limit: an unexpired override, else the metric default; None for no metric.

        Expiry uses the DB clock. Metric status is ignored: ADM-042 calls this inside its consume
        transaction and owns the refusal of archived metrics (ADR-0024 point 1).
        """
        return await self.session.scalar(
            select(func.coalesce(_O.limit_value, UsageMetric.default_limit))
            .select_from(UsageMetric)
            .outerjoin(
                _O,
                and_(_O.metric_id == UsageMetric.id, _O.user_id == user_id, _ACTIVE),
            )
            .where(UsageMetric.id == metric_id)
        )
