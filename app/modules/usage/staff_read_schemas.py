"""Response bodies of the staff usage reads (ADM-043)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class StaffMetricOverride(BaseModel):
    limit: int
    expires_at: datetime | None
    active: bool


class StaffMetricUsage(BaseModel):
    metric: str
    display_name: str
    unit: str
    status: Literal["active", "archived"]
    period: str
    period_start: datetime
    period_end: datetime
    limit: int
    used: int
    remaining: int
    override: StaffMetricOverride | None


class StaffUserUsageRead(BaseModel):
    user_id: uuid.UUID
    member: bool
    metrics: list[StaffMetricUsage]


class UsagePeriodTotalRead(BaseModel):
    metric: str
    period: str
    period_start: datetime
    period_end: datetime
    used: int


class UsageEventRead(BaseModel):
    id: uuid.UUID
    metric: str
    amount: int
    settled_amount: int | None
    charged: int
    state: str
    period: str
    period_start: datetime
    period_end: datetime
    created_at: datetime
    settled_at: datetime | None
    refunded_at: datetime | None
