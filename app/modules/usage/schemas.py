import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.core.text import nfc_if_str, reject_disallowed_categories
from app.modules.usage.models import (
    MAX_LIMIT,
    METRIC_KEY_RE,
    UNIT_RE,
    UsageMetricStatus,
    UsagePeriod,
)


def _trimmed(max_length: int) -> Callable[[str], str]:
    """Strip, then 1..max_length (pydantic checks `min_length` before it strips)."""

    def check(value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= max_length:
            raise PydanticCustomError("display_text", f"must be 1-{max_length} characters")
        return value

    return check


# Trimmed, not blank, NFC, no control/bidi characters. Reused by ADM-040/044.
Reason = Annotated[
    str,
    BeforeValidator(nfc_if_str),
    AfterValidator(_trimmed(500)),
    AfterValidator(reject_disallowed_categories),
    Field(description="Why the change is made (1-500 chars); stored in the audit entry only."),
]
DisplayName = Annotated[
    str,
    BeforeValidator(nfc_if_str),
    AfterValidator(_trimmed(100)),
    AfterValidator(reject_disallowed_categories),
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
