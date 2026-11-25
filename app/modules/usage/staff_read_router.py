"""Staff (Bearer) reads of a user's usage — R3 class 1 (ADM-043, spec OQ65/OQ69)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import AwareDatetime

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import current_admin, require_permission
from app.modules.rbac.models import APP_KEY_RE
from app.modules.usage.meter_schemas import MetricKey
from app.modules.usage.models import UsageEventState
from app.modules.usage.router import READ
from app.modules.usage.staff_read_schemas import (
    StaffUserUsageRead,
    UsageEventRead,
    UsagePeriodTotalRead,
)
from app.modules.usage.staff_read_service import StaffUsageReadService

router = APIRouter(
    prefix="/apps/{key}/user-usage", tags=["usage"], dependencies=[Depends(current_admin)]
)

_404 = (
    "Unknown app (the `admin` app has no usage), unknown user, or unknown `metric` "
    "(code usage_metric_not_found; archived metrics are found)"
)

KeyPath = Annotated[str, Path(pattern=APP_KEY_RE)]
MetricQuery = Annotated[MetricKey | None, Query()]
FromQuery = Annotated[AwareDatetime | None, Query(alias="from")]
ToQuery = Annotated[AwareDatetime | None, Query()]


@router.get(
    "/{user_id}",
    response_model=StaffUserUsageRead,
    description=(
        "Current period per metric (all statuses, `key` order): effective limit, used, "
        "remaining, and the user's override (`active: false` once expired). Non-members get "
        "200 with `member: false`."
    ),
    **require_permission(READ, errors={404: _404}),
)
async def get_user_usage(
    session: SessionDep, key: KeyPath, user_id: uuid.UUID, metric: MetricQuery = None
) -> StaffUserUsageRead:
    return await StaffUsageReadService(session).current(key, user_id, metric)


@router.get(
    "/{user_id}/periods",
    response_model=Page[UsagePeriodTotalRead],
    description=(
        "Per-period totals, newest first. `used` is the period total; the limit that applied "
        "then is not stored. `from` (inclusive) and `to` (exclusive) filter `period_start`."
    ),
    **require_permission(READ, errors={404: _404}),
)
async def list_user_usage_periods(
    session: SessionDep,
    page: PageDep,
    key: KeyPath,
    user_id: uuid.UUID,
    metric: MetricQuery = None,
    from_: FromQuery = None,
    to: ToQuery = None,
) -> Page[UsagePeriodTotalRead]:
    items, total = await StaffUsageReadService(session).periods(
        key, user_id, metric, from_, to, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/{user_id}/events",
    response_model=Page[UsageEventRead],
    description=(
        "Usage ledger, newest first. Events are kept 90 days (OQ69, purge ADM-045); "
        "`idempotency_key` is never returned. `from` (inclusive) and `to` (exclusive) filter "
        "`created_at`."
    ),
    **require_permission(READ, errors={404: _404}),
)
async def list_user_usage_events(
    session: SessionDep,
    page: PageDep,
    key: KeyPath,
    user_id: uuid.UUID,
    metric: MetricQuery = None,
    from_: FromQuery = None,
    to: ToQuery = None,
    state: UsageEventState | None = None,
) -> Page[UsageEventRead]:
    items, total = await StaffUsageReadService(session).events(
        key, user_id, metric, from_, to, state, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)
