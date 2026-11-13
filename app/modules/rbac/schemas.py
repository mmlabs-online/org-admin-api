import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.text import nfc_if_str, reject_disallowed_categories
from app.modules.rbac.models import (
    APP_KEY_RE,
    PERMISSION_KEY_RE,
    ROLE_KEY_RE,
    AppStatus,
    JoinPolicy,
    OrgMode,
)
from app.modules.rbac.origins import MAX_ORIGINS, normalize_origins

# Display text (input only): NFC, then length, then Unicode category check (ADR-0003 §11).
AppName = Annotated[
    str,
    BeforeValidator(nfc_if_str),
    Field(min_length=1, max_length=255),
    AfterValidator(reject_disallowed_categories),
]


class AppCreate(BaseModel):
    """New apps are `active`; their default role is the built `<key>:member` (no input for it)."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=APP_KEY_RE, max_length=32)
    name: AppName
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

    name: AppName | None = None
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


PermissionKey = Annotated[str, Field(pattern=PERMISSION_KEY_RE, max_length=160)]
Description = Annotated[
    str,
    BeforeValidator(nfc_if_str),
    Field(max_length=500),
    AfterValidator(reject_disallowed_categories),
]
RoleName = Annotated[
    str,
    BeforeValidator(nfc_if_str),
    Field(min_length=1, max_length=255),
    AfterValidator(reject_disallowed_categories),
]


class AppRegister(BaseModel):
    """Operator CLI input (USR-07): `None` policy/mode = keep the stored value (or the default)."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=APP_KEY_RE, max_length=32)
    name: AppName
    origins: list[str] = Field(min_length=1, max_length=MAX_ORIGINS)
    join_policy: JoinPolicy | None = None
    org_mode: OrgMode | None = None
    permissions: list[PermissionKey] = Field(default_factory=list, max_length=50)

    @field_validator("origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        return normalize_origins(value)


class PermissionCreate(BaseModel):
    """`is_admin` absent or null: false, except `<app>:*` which is always stored `true`."""

    model_config = ConfigDict(extra="forbid")

    key: PermissionKey
    description: Description | None = None
    is_admin: bool | None = None


class PermissionUpdate(BaseModel):
    """Partial update; `key` is immutable. Explicit null is accepted only for `description`."""

    model_config = ConfigDict(extra="forbid")

    description: Description | None = None
    is_admin: bool | None = None

    @model_validator(mode="after")
    def _non_null(self) -> Self:
        if "is_admin" in self.model_fields_set and self.is_admin is None:
            raise ValueError("is_admin must not be null")
        return self


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    description: str | None
    is_admin: bool


class RoleCreate(BaseModel):
    """New roles are never `is_system` and hold no permissions (use the PUT)."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=ROLE_KEY_RE, max_length=160)
    name: RoleName


class RoleUpdate(BaseModel):
    """Partial update of the display name; `key` and `is_system` are immutable."""

    model_config = ConfigDict(extra="forbid")

    name: RoleName | None = None

    @model_validator(mode="after")
    def _non_null(self) -> Self:
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name must not be null")
        return self


class RoleRead(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    is_system: bool
    permissions: list[str]  # sorted permission keys


class RolePermissionsReplace(BaseModel):
    """The role's full permission set; duplicates are ignored, `[]` empties the role."""

    model_config = ConfigDict(extra="forbid")

    permission_keys: list[PermissionKey] = Field(max_length=200)


class RoleCatalogEntry(BaseModel):
    key: str
    name: str
    is_system: bool
    permissions: list[str]  # sorted permission keys


class RoleCatalog(BaseModel):
    """Public role catalog (D17). Field order is part of the ETag; append-only."""

    app: str
    roles: list[RoleCatalogEntry]  # sorted by key


class AssignmentCreate(BaseModel):
    """Assign a role at app scope (`org_id` null) or in one organization; `granted_by` is never
    input (it is the actor once ADM-002 lands)."""

    model_config = ConfigDict(extra="forbid")

    role_id: uuid.UUID
    org_id: uuid.UUID | None = None
    expires_at: AwareDatetime | None = None


class AssignmentRead(BaseModel):
    id: uuid.UUID
    app: str  # app key
    role_id: uuid.UUID
    role_key: str
    org_id: uuid.UUID | None
    granted_by: uuid.UUID | None
    expires_at: datetime | None
    active: bool  # computed with the database clock
    created_at: datetime
