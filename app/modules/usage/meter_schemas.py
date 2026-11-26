"""Request and response bodies of the app-facing usage routes (ADM-042, ADR-0022)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.modules.usage.models import IDEMPOTENCY_KEY_RE, METRIC_KEY_RE

MetricKey = Annotated[str, Field(pattern=METRIC_KEY_RE)]
IdempotencyKey = Annotated[str, Field(pattern=IDEMPOTENCY_KEY_RE)]
# The per-call maximum is a setting (checked in the service); this is its ceiling.
Amount = Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]


class UsageConsume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    metric: MetricKey
    amount: Amount
    idempotency_key: IdempotencyKey


class UsageSettle(UsageConsume):
    """`amount` is the actual amount, replacing the estimate that was consumed."""


class UsageRefund(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    metric: MetricKey
    idempotency_key: IdempotencyKey


class UsageEventResult(BaseModel):
    user_id: uuid.UUID
    metric: str
    amount: int
    state: str
    charged: int
    period: str
    period_start: datetime
    period_end: datetime
    limit: int
    used: int
    remaining: int
    replayed: bool


class UserMetricUsage(BaseModel):
    metric: str
    display_name: str
    unit: str
    period: str
    period_start: datetime
    period_end: datetime
    limit: int
    used: int
    remaining: int


class UserUsageRead(BaseModel):
    user_id: uuid.UUID
    metrics: list[UserMetricUsage]
