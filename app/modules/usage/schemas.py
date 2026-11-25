import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from app.core.text import DisplayName, nfc_if_str, reject_disallowed_categories, trimmed
from app.modules.usage.models import (
    MAX_LIMIT,
    METRIC_KEY_RE,
    UNIT_RE,
    UsageMetricStatus,
    UsagePeriod,
)

# Trimmed, not blank, NFC, no control/bidi characters. Reused by ADM-040/044.
Reason = Annotated[
    str,
    BeforeValidator(nfc_if_str),
    AfterValidator(trimmed(500)),
    AfterValidator(reject_disallowed_categories),
    Field(description="Why the change is made (1-500 chars); stored in the audit entry only."),
]
Limit = Annotated[int, Field(strict=True, ge=0, le=MAX_LIMIT)]


class UsageMetricCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Annotated[str, Field(pattern=METRIC_KEY_RE)]
    display_name: DisplayName
    unit: Annotated[str, Field(pattern=UNIT_RE)]
    default_limit: Limit
    period: UsagePeriod = UsagePeriod.WEEK
    reason: Reason


class UsageMetricUpdate(BaseModel):
    """An absent field is unchanged; `key` and `unit` are immutable (extra fields, 422)."""

    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    default_limit: Limit | None = None
    period: UsagePeriod | None = None
    status: UsageMetricStatus | None = None
    reason: Reason

    @model_validator(mode="before")
    @classmethod
    def _no_null_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and any(v is None for v in data.values()):
            raise ValueError("fields cannot be null")
        return data

    @model_validator(mode="after")
    def _has_a_change(self) -> "UsageMetricUpdate":
        if not (self.model_fields_set - {"reason"}):
            raise ValueError("at least one field besides `reason` is required")
        return self


class UsageMetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_key: str
    key: str
    display_name: str
    unit: str
    default_limit: int
    period: UsagePeriod
    status: UsageMetricStatus
    created_at: datetime
    updated_at: datetime


class UsageLimitOverrideSet(BaseModel):
    """Full replacement of the user's override for the metric (OQ62)."""

    model_config = ConfigDict(extra="forbid")

    limit: Limit
    expires_at: AwareDatetime | None = None
    reason: Reason


class UsageLimitOverrideRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class UsageLimitOverrideRead(BaseModel):
    """No `reason`: it is stored in the audit entry only (ADR-0024 point 5)."""

    model_config = ConfigDict(from_attributes=True)

    app_key: str
    metric_id: uuid.UUID
    user_id: uuid.UUID
    limit: int
    expires_at: datetime | None
    active: bool
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
