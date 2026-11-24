"""App-facing usage metering: read, consume, settle, refund (ADM-042, ADR-0022). HTTP-free (R1).

Uses are not audit entries (ADR-0022 §9). Logs carry key id, user id and metric key only; the
idempotency key and emails are never logged (R11). Lock order everywhere: event row, then
counter row, so no deadlock cycle.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import UnprocessableError
from app.modules.app_credentials.service import AuthenticatedApp
from app.modules.rbac.repository import RbacRepository
from app.modules.usage import metering
from app.modules.usage.errors import (
    IdempotencyKeyReusedError,
    UsageAdjustWindowClosedError,
    UsageEventNotFoundError,
    UsageEventStateError,
    UsageLimitExceededError,
    UsageMetricNotFoundError,
    UsageUserDeniedError,
)
from app.modules.usage.meter_repository import UsageMeterRepository
from app.modules.usage.meter_schemas import (
    UsageConsume,
    UsageEventResult,
    UsageRefund,
    UsageSettle,
    UserMetricUsage,
    UserUsageRead,
)
from app.modules.usage.models import (
    UsageEvent,
    UsageEventState,
    UsageMetric,
    UsageMetricStatus,
    UsagePeriod,
)
from app.modules.usage.periods import period_bounds

logger = logging.getLogger(__name__)


class UsageMeterService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.repo = UsageMeterRepository(session)
        self.rbac = RbacRepository(session)
        self.settings = settings

    async def _active_metric(self, caller: AuthenticatedApp, key: str) -> UsageMetric:
        metric = await self.repo.find_metric(caller.app_id, key)
        if metric is None or metric.status != UsageMetricStatus.ACTIVE.value:
            raise UsageMetricNotFoundError("Metric not found")
        return metric

    async def _require_member(self, caller: AuthenticatedApp, user_id: uuid.UUID) -> None:
        if not await self.rbac.is_active_app_member(user_id, caller.app_id):
            logger.info("usage_denied key_id=%s user_id=%s", caller.key_id, user_id)
            raise UsageUserDeniedError("The user cannot use this app")

    def _check_amount(self, amount: int) -> None:
        if amount > self.settings.usage_max_amount_per_call:
            raise UnprocessableError(
                "Amount is above the per-call maximum",
                details=[{"field": "amount", "reason": "too_large"}],
            )

    async def read(
        self, caller: AuthenticatedApp, user_id: uuid.UUID, metric_key: str | None
    ) -> UserUsageRead:
        if metric_key is not None:
            await self._active_metric(caller, metric_key)
        await self._require_member(caller, user_id)
        now = await self.repo.db_now()
        bounds = {kind.value: period_bounds(kind, now) for kind in UsagePeriod}
        rows = await self.repo.read_user_usage(
            user_id, caller.app_id, {k: v[0] for k, v in bounds.items()}, metric_key
        )
        return UserUsageRead(
            user_id=user_id,
            metrics=[
                UserMetricUsage(
                    metric=r.key,
                    display_name=r.display_name,
                    unit=r.unit,
                    period=r.period,
                    period_start=bounds[r.period][0],
                    period_end=bounds[r.period][1],
                    limit=r.limit,
                    used=r.used,
                    remaining=metering.remaining(r.limit, r.used),
                )
                for r in rows
            ],
        )

    def _result(
        self,
        event: UsageEvent,
        metric_key: str,
        used: int,
        limit: int,
        *,
        replayed: bool,
    ) -> UsageEventResult:
        kind, start, end = metering.counter_period(event.period, event.period_start)
        return UsageEventResult(
            user_id=event.user_id,
            metric=metric_key,
            amount=event.amount,
            state=event.state,
            charged=metering.charged(event.state, event.amount, event.settled_amount),
            period=kind.value,
            period_start=start,
            period_end=end,
            limit=limit,
            used=used,
            remaining=metering.remaining(limit, used),
            replayed=replayed,
        )

    async def _snapshot_result(
        self, event: UsageEvent, metric_key: str, *, replayed: bool
    ) -> UsageEventResult:
        used, limit = await self.repo.snapshot(
            user_id=event.user_id,
            metric_id=event.metric_id,
            period=event.period,
            period_start=event.period_start,
        )
        return self._result(event, metric_key, used, limit, replayed=replayed)

    async def consume(self, caller: AuthenticatedApp, body: UsageConsume) -> UsageEventResult:
        await self.repo.set_lock_timeout()
        metric = await self._active_metric(caller, body.metric)
        await self._require_member(caller, body.user_id)
        self._check_amount(body.amount)
        now = await self.repo.db_now()
        kind, start, _ = metering.counter_period(metric.period, now)
        event = await self.repo.insert_event(
            app_id=caller.app_id,
            user_id=body.user_id,
            metric_id=metric.id,
            idempotency_key=body.idempotency_key,
            period=kind.value,
            period_start=start,
            amount=body.amount,
        )
        if event is None:  # a retry: the first result, the limit is not checked again
            event = await self.repo.get_event(caller.app_id, body.idempotency_key, for_update=False)
            assert event is not None  # noqa: S101 (the conflict proves the row exists)
            metering.replay_or_conflict(
                stored=(event.user_id, event.metric_id, event.amount),
                user_id=body.user_id,
                metric_id=metric.id,
                amount=body.amount,
            )
            return await self._snapshot_result(event, metric.key, replayed=True)
        charged = await self.repo.consume_counter(
            app_id=caller.app_id,
            user_id=body.user_id,
            metric_id=metric.id,
            period=kind.value,
            period_start=start,
            amount=body.amount,
        )
        if charged is None:
            await self.repo.discard_event(event.id)  # refusals are not stored
            used, limit = await self.repo.snapshot(
                user_id=body.user_id, metric_id=metric.id, period=kind.value, period_start=start
            )
            raise UsageLimitExceededError(
                "Usage limit exceeded",
                details=[
                    {
                        "field": "amount",
                        "reason": "over_limit",
                        "limit": limit,
                        "used": used,
                        "remaining": metering.remaining(limit, used),
                    }
                ],
            )
        return self._result(event, metric.key, *charged, replayed=False)

    async def _load_event(
        self, caller: AuthenticatedApp, key: str, user_id: uuid.UUID, metric_key: str
    ) -> tuple[UsageEvent, str]:
        event = await self.repo.get_event(caller.app_id, key, for_update=True)
        if event is None:
            raise UsageEventNotFoundError("Usage event not found")
        metric = await self.repo.find_metric(caller.app_id, metric_key)
        if metric is None or (event.user_id, event.metric_id) != (user_id, metric.id):
            raise IdempotencyKeyReusedError("The idempotency key was used with a different request")
        return event, metric.key

    async def _require_window(self, event: UsageEvent, now: datetime) -> None:
        if not metering.window_open(
            event.created_at, now, self.settings.usage_adjust_window_seconds
        ):
            raise UsageAdjustWindowClosedError("The settle and refund window has closed")

    async def settle(self, caller: AuthenticatedApp, body: UsageSettle) -> UsageEventResult:
        await self.repo.set_lock_timeout()
        self._check_amount(body.amount)
        event, metric_key = await self._load_event(
            caller, body.idempotency_key, body.user_id, body.metric
        )
        if event.state == UsageEventState.REFUNDED.value:
            raise UsageEventStateError("The usage event was refunded")
        if event.state == UsageEventState.SETTLED.value:
            if event.settled_amount != body.amount:
                raise IdempotencyKeyReusedError(
                    "The idempotency key was used with a different request"
                )
            return await self._snapshot_result(event, metric_key, replayed=True)
        now = await self.repo.db_now()
        await self._require_window(event, now)
        delta = metering.settle_delta(event.amount, body.amount)
        if delta and not await self.repo.adjust_counter(
            user_id=event.user_id,
            metric_id=event.metric_id,
            period=event.period,
            period_start=event.period_start,
            delta=delta,
        ):
            raise UnprocessableError(
                "Amount is out of range", details=[{"field": "amount", "reason": "out_of_range"}]
            )
        event.state = UsageEventState.SETTLED.value
        event.settled_amount = body.amount
        event.settled_at = now
        await self.repo.save_event()
        return await self._snapshot_result(event, metric_key, replayed=False)

    async def refund(self, caller: AuthenticatedApp, body: UsageRefund) -> UsageEventResult:
        await self.repo.set_lock_timeout()
        event, metric_key = await self._load_event(
            caller, body.idempotency_key, body.user_id, body.metric
        )
        if event.state == UsageEventState.REFUNDED.value:
            return await self._snapshot_result(event, metric_key, replayed=True)
        now = await self.repo.db_now()
        await self._require_window(event, now)
        credit = metering.charged(event.state, event.amount, event.settled_amount)
        await self.repo.adjust_counter(  # a credit never exceeds MAX_LIMIT, never below 0
            user_id=event.user_id,
            metric_id=event.metric_id,
            period=event.period,
            period_start=event.period_start,
            delta=-credit,
        )
        event.state = UsageEventState.REFUNDED.value
        event.refunded_at = now
        await self.repo.save_event()
        logger.info(
            "usage_refund key_id=%s user_id=%s metric=%s amount=%s",
            caller.key_id,
            event.user_id,
            metric_key,
            credit,
        )
        return await self._snapshot_result(event, metric_key, replayed=False)
