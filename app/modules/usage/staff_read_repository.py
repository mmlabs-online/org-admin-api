"""SQL of the staff usage reads (ADM-043). SQLAlchemy only; read-only."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Select, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.models import (
    UsageCounter,
    UsageEvent,
    UsageLimitOverride,
    UsageMetric,
)


@dataclass(frozen=True)
class StaffMetricRow:
    key: str
    display_name: str
    unit: str
    status: str
    period: str
    limit: int
    used: int
    override_limit: int | None
    override_expires_at: datetime | None
    override_active: bool


@dataclass(frozen=True)
class PeriodRow:
    metric: str
    period: str
    period_start: datetime
    used: int


@dataclass(frozen=True)
class EventRow:
    id: uuid.UUID
    metric: str
    amount: int
    settled_amount: int | None
    state: str
    period: str
    period_start: datetime
    created_at: datetime
    settled_at: datetime | None
    refunded_at: datetime | None


class StaffUsageReadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current(
        self,
        app_id: uuid.UUID,
        user_id: uuid.UUID,
        starts: dict[str, datetime],
        metric_id: uuid.UUID | None,
    ) -> list[StaffMetricRow]:
        """All metrics of the app (any status) with the user's limit and used, in one query."""
        o, m, c = UsageLimitOverride, UsageMetric, UsageCounter
        start = case(
            {k: literal(v, DateTime(timezone=True)) for k, v in starts.items()}, value=m.period
        )
        active = case(
            (o.id.is_(None), False), else_=(o.expires_at.is_(None)) | (o.expires_at > func.now())
        )
        stmt = (
            select(
                m.key,
                m.display_name,
                m.unit,
                m.status,
                m.period,
                case((active, o.limit_value), else_=m.default_limit),
                func.coalesce(c.used, 0),
                o.limit_value,
                o.expires_at,
                active,
            )
            .select_from(m)
            .outerjoin(o, (o.metric_id == m.id) & (o.user_id == user_id))
            .outerjoin(
                c,
                (c.metric_id == m.id)
                & (c.user_id == user_id)
                & (c.period == m.period)
                & (c.period_start == start),
            )
            .where(m.app_id == app_id)
            .order_by(m.key)
        )
        if metric_id is not None:
            stmt = stmt.where(m.id == metric_id)
        return [StaffMetricRow(*r) for r in await self.session.execute(stmt)]

    async def _page(
        self, stmt: Select[*tuple[Any, ...]], limit: int, offset: int
    ) -> tuple[list[tuple[object, ...]], int]:
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = await self.session.execute(stmt.limit(limit).offset(offset))
        return [tuple(r) for r in rows], total or 0

    async def page_periods(
        self,
        app_id: uuid.UUID,
        user_id: uuid.UUID,
        metric_id: uuid.UUID | None,
        from_: datetime | None,
        to: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PeriodRow], int]:
        c, m = UsageCounter, UsageMetric
        where = [c.app_id == app_id, c.user_id == user_id]
        if metric_id is not None:
            where.append(c.metric_id == metric_id)
        if from_ is not None:
            where.append(c.period_start >= from_)
        if to is not None:
            where.append(c.period_start < to)
        stmt = (
            select(m.key, c.period, c.period_start, c.used)
            .join(m, m.id == c.metric_id)
            .where(*where)
            .order_by(c.period_start.desc(), m.key, c.period)
        )
        rows, total = await self._page(stmt, limit, offset)
        return [PeriodRow(*r) for r in rows], total  # type: ignore[arg-type]

    async def page_events(
        self,
        app_id: uuid.UUID,
        user_id: uuid.UUID,
        metric_id: uuid.UUID | None,
        from_: datetime | None,
        to: datetime | None,
        state: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[EventRow], int]:
        e, m = UsageEvent, UsageMetric
        where = [e.app_id == app_id, e.user_id == user_id]
        if metric_id is not None:
            where.append(e.metric_id == metric_id)
        if from_ is not None:
            where.append(e.created_at >= from_)
        if to is not None:
            where.append(e.created_at < to)
        if state is not None:
            where.append(e.state == state)
        stmt = (
            select(
                e.id,
                m.key,
                e.amount,
                e.settled_amount,
                e.state,
                e.period,
                e.period_start,
                e.created_at,
                e.settled_at,
                e.refunded_at,
            )
            .join(m, m.id == e.metric_id)
            .where(*where)
            .order_by(e.created_at.desc(), e.id.desc())
        )
        rows, total = await self._page(stmt, limit, offset)
        return [EventRow(*r) for r in rows], total  # type: ignore[arg-type]
