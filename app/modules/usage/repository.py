import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import bumped_updated_at
from app.modules.usage.models import UsageMetric


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
