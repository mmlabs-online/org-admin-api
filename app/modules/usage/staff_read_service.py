"""Staff reads of a user's usage: current period, per-period history, 90-day ledger (ADM-043).
HTTP-free (R1). Logs nothing; staff reads are not audit entries."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableError
from app.modules.rbac.repository import RbacRepository
from app.modules.usage import metering
from app.modules.usage.errors import UsageMetricNotFoundError
from app.modules.usage.meter_repository import UsageMeterRepository
from app.modules.usage.models import UsageEventState, UsagePeriod
from app.modules.usage.periods import period_bounds
from app.modules.usage.service import resolve_app
from app.modules.usage.staff_read_repository import StaffUsageReadRepository
from app.modules.usage.staff_read_schemas import (
    StaffMetricOverride,
    StaffMetricUsage,
    StaffUserUsageRead,
    UsageEventRead,
    UsagePeriodTotalRead,
)
from app.modules.users.repository import UserRepository


def check_range(from_: datetime | None, to: datetime | None) -> None:
    if from_ is not None and to is not None and from_ >= to:
        raise UnprocessableError(
            "`from` must be before `to`",
            details=[{"field": "from", "reason": "not_before_to"}],
        )


class StaffUsageReadService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = StaffUsageReadRepository(session)
        self.meter = UsageMeterRepository(session)
        self.rbac = RbacRepository(session)
        self.users = UserRepository(session)

    async def _resolve(
        self, app_key: str, user_id: uuid.UUID, metric_key: str | None
    ) -> tuple[uuid.UUID, uuid.UUID | None]:
        """(app id, metric id or None); 404 for an unknown app, user or metric (any status)."""
        app = await resolve_app(self.rbac, app_key)
        if await self.users.get(user_id) is None:
            raise NotFoundError("User not found")
        if metric_key is None:
            return app.id, None
        metric = await self.meter.find_metric(app.id, metric_key)
        if metric is None:
            raise UsageMetricNotFoundError("Metric not found")
        return app.id, metric.id

    async def current(
        self, app_key: str, user_id: uuid.UUID, metric_key: str | None
    ) -> StaffUserUsageRead:
        app_id, metric_id = await self._resolve(app_key, user_id, metric_key)
        now = await self.meter.db_now()
        bounds = {kind.value: period_bounds(kind, now) for kind in UsagePeriod}
        rows = await self.repo.current(
            app_id, user_id, {k: v[0] for k, v in bounds.items()}, metric_id
        )
        return StaffUserUsageRead(
            user_id=user_id,
            member=await self.rbac.is_active_app_member(user_id, app_id),
            metrics=[
                StaffMetricUsage(
                    metric=r.key,
                    display_name=r.display_name,
                    unit=r.unit,
                    status=r.status,
                    period=r.period,
                    period_start=bounds[r.period][0],
                    period_end=bounds[r.period][1],
                    limit=r.limit,
                    used=r.used,
                    remaining=metering.remaining(r.limit, r.used),
                    override=None
                    if r.override_limit is None
                    else StaffMetricOverride(
                        limit=r.override_limit,
                        expires_at=r.override_expires_at,
                        active=r.override_active,
                    ),
                )
                for r in rows
            ],
        )

    async def periods(
        self,
        app_key: str,
        user_id: uuid.UUID,
        metric_key: str | None,
        from_: datetime | None,
        to: datetime | None,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[UsagePeriodTotalRead], int]:
        check_range(from_, to)
        app_id, metric_id = await self._resolve(app_key, user_id, metric_key)
        rows, total = await self.repo.page_periods(
            app_id, user_id, metric_id, from_, to, limit, offset
        )
        items = []
        for r in rows:
            kind, start, end = metering.counter_period(r.period, r.period_start)
            items.append(
                UsagePeriodTotalRead(
                    metric=r.metric,
                    period=kind.value,
                    period_start=start,
                    period_end=end,
                    used=r.used,
                )
            )
        return items, total

    async def events(
        self,
        app_key: str,
        user_id: uuid.UUID,
        metric_key: str | None,
        from_: datetime | None,
        to: datetime | None,
        state: UsageEventState | None,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[UsageEventRead], int]:
        check_range(from_, to)
        app_id, metric_id = await self._resolve(app_key, user_id, metric_key)
        rows, total = await self.repo.page_events(
            app_id,
            user_id,
            metric_id,
            from_,
            to,
            None if state is None else state.value,
            limit,
            offset,
        )
        items = []
        for r in rows:
            kind, start, end = metering.counter_period(r.period, r.period_start)
            items.append(
                UsageEventRead(
                    id=r.id,
                    metric=r.metric,
                    amount=r.amount,
                    settled_amount=r.settled_amount,
                    charged=metering.charged(r.state, r.amount, r.settled_amount),
                    state=r.state,
                    period=kind.value,
                    period_start=start,
                    period_end=end,
                    created_at=r.created_at,
                    settled_at=r.settled_at,
                    refunded_at=r.refunded_at,
                )
            )
        return items, total
