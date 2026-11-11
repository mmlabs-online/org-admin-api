import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.rbac.models import APP_KEY_RE, AppStatus, JoinPolicy, OrgMode
from app.modules.rbac.origins import MAX_ORIGINS, normalize_origins


class AppCreate(BaseModel):
    """New apps are `active`; their default role is the built `<key>:member` (no input for it)."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=APP_KEY_RE, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    join_policy: JoinPolicy = JoinPolicy.OPEN
    org_mode: OrgMode = OrgMode.NONE
    origins: list[str] = Field(default_factory=list, max_length=MAX_ORIGINS)

    @field_validator("origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        return normalize_origins(value)


class AppUpdate(BaseModel):
    """Partial update; `key` is immutable. Explicit null is accepted only for `default_role_id`."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: AppStatus | None = None
    join_policy: JoinPolicy | None = None
    org_mode: OrgMode | None = None
    origins: list[str] | None = Field(default=None, max_length=MAX_ORIGINS)
    default_role_id: uuid.UUID | None = None

    @field_validator("origins")
    @classmethod
    def _origins(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_origins(value)

    @model_validator(mode="after")
    def _null_only_for_default_role(self) -> Self:
        for field in self.model_fields_set - {"default_role_id"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class AppRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    status: AppStatus
    join_policy: JoinPolicy
    org_mode: OrgMode
    origins: list[str]
    default_role_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
