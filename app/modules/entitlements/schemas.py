import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints

from app.modules.rbac.models import APP_KEY_RE

PLAN_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
LIMIT_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

Plan = Annotated[str, StringConstraints(pattern=PLAN_PATTERN)]
Limits = Annotated[
    dict[
        Annotated[str, StringConstraints(pattern=LIMIT_KEY_PATTERN)],
        Annotated[StrictInt, Field(ge=0, le=2**53 - 1)],
    ],
    Field(max_length=32),
]


class EntitlementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_key: Annotated[str, StringConstraints(pattern=APP_KEY_RE, max_length=32)]
    plan: Plan
    limits: Limits = Field(default_factory=dict)
    enabled: StrictBool = True


class EntitlementUpdate(BaseModel):
    """An explicit `null` means no change; `limits` replaces the whole object."""

    model_config = ConfigDict(extra="forbid")

    plan: Plan | None = None
    limits: Limits | None = None
    enabled: StrictBool | None = None


class EntitlementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    app_key: str
    plan: str
    limits: dict[str, int]
    enabled: bool
    active: bool
    created_at: datetime
    updated_at: datetime
