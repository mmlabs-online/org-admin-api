import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

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
    PermissionScope,
)
from app.modules.rbac.origins import MAX_ORIGINS, normalize_origins
from app.modules.users.models import UserStatus

AppSort = Literal["key", "-key", "name", "-name", "created_at", "-created_at"]
DEFAULT_APP_SORT: AppSort = "key"

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
    """Partial update; `key` is immutable. Null only for the role ids."""

    model_config = ConfigDict(extra="forbid")

    name: AppName | None = None
    status: AppStatus | None = None
    join_policy: JoinPolicy | None = None
    org_mode: OrgMode | None = None
    origins: list[str] | None = Field(default=None, max_length=MAX_ORIGINS)
    default_role_id: uuid.UUID | None = None
    org_owner_role_id: uuid.UUID | None = None
    super_admin_role_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Designate (or, with null, clear) the app's super-admin role (ADR-0030). Needs "
            "admin:* when the value changes."
        ),
    )

    @field_validator("origins")
    @classmethod
    def _origins(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_origins(value)

    @model_validator(mode="after")
    def _null_only_for_default_role(self) -> Self:
        for field in self.model_fields_set - {
            "default_role_id",
            "org_owner_role_id",
            "super_admin_role_id",
        }:
            if getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class AppFields(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    status: AppStatus
    join_policy: JoinPolicy
    org_mode: OrgMode
    origins: list[str]
    default_role_id: uuid.UUID | None
    org_owner_role_id: uuid.UUID | None = None
    super_admin_role_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class AppRead(AppFields):
    member_count: int = Field(
        ge=0,
        description=(
            "Distinct active users with at least one non-expired role assignment on the app "
            "(app scope or any org); unverified email counts; `admin` = staff (OQ5)."
        ),
    )


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
    # `scope = org` permissions (org apps only); `permissions` are `scope = app` (ADR-0015).
    org_permissions: list[PermissionKey] = Field(default_factory=list, max_length=50)

    @field_validator("origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        return normalize_origins(value)


class AdminOriginsSet(BaseModel):
    """Operator CLI input (ADM-002c): the full origin set of the `admin` app; `[]` clears it."""

    model_config = ConfigDict(extra="forbid")

    origins: list[str] = Field(default_factory=list, max_length=MAX_ORIGINS)

    @field_validator("origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        return normalize_origins(value)


class PermissionCreate(BaseModel):
    """`is_admin` absent or null: false, except `<app>:*` and admin-app permissions, which are
    always stored `true`. `scope` absent or null: the reserved key decides, else `org` on
    `org_mode = required` apps and `app` otherwise (ADR-0015)."""

    model_config = ConfigDict(extra="forbid")

    key: PermissionKey
    description: Description | None = None
    is_admin: bool | None = None
    scope: PermissionScope | None = None


class PermissionUpdate(BaseModel):
    """Partial update; `key` is immutable. Explicit null is accepted only for `description`."""

    model_config = ConfigDict(extra="forbid")

    description: Description | None = None
    is_admin: bool | None = None
    scope: PermissionScope | None = None

    @model_validator(mode="after")
    def _non_null(self) -> Self:
        for name in ("is_admin", "scope"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        return self


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    description: str | None
    is_admin: bool
    scope: PermissionScope


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
    updated_at: datetime = Field(
        description="Last change to the role row, including its permission set (USR-24)."
    )


class RoleDefinitionRead(BaseModel):
    """Staff read model of a role (USR-16): any app, `admin` and disabled ones included."""

    id: uuid.UUID
    app: str
    key: str
    name: str
    is_system: bool
    is_default: bool = Field(
        description="This role is the app's default role (apps.default_role_id)."
    )
    is_super_admin: bool = Field(
        description="This role is the app's designated super-admin role (apps.super_admin_role_id)."
    )
    privileged: bool = Field(
        description=(
            "App is `admin`, the role holds a permission with is_admin, or it is the app's "
            "designated super-admin role. Granting or revoking it needs admin:* (ADR-0008 G4, "
            "ADR-0030). UI hint; the server enforces on every write."
        )
    )
    permissions: list[str]  # sorted permission keys
    created_at: datetime
    updated_at: datetime = Field(
        description=(
            "Last change to the role row, including its permission set (USR-24). "
            "ETag source: `version_etag(updated_at)`."
        )
    )


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


ASSIGNMENT_BATCH_MAX_USERS = 100


class AssignmentBatchCreate(BaseModel):
    """Assign one role to many users; `org_id` and `expires_at` apply to every user. Duplicate
    ids are refused by the service (422 `duplicate_user_ids`), not here."""

    model_config = ConfigDict(extra="forbid")

    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=ASSIGNMENT_BATCH_MAX_USERS)
    org_id: uuid.UUID | None = None
    expires_at: AwareDatetime | None = None


class BatchItemStatus(StrEnum):
    CREATED = "created"
    UNCHANGED = "unchanged"
    FAILED = "failed"


class AssignmentBatchError(BaseModel):
    code: str
    message: str


class AssignmentBatchItem(BaseModel):
    """Every key is always present (null when not applicable)."""

    user_id: uuid.UUID
    status: BatchItemStatus
    assignment_id: uuid.UUID | None
    expires_at: datetime | None
    error: AssignmentBatchError | None


class AssignmentBatchRead(BaseModel):
    batch_id: uuid.UUID = Field(description="The request id; also on every audit row of the batch.")
    results: list[AssignmentBatchItem] = Field(description="One result per user, request order.")


class RoleHolderRead(AssignmentRead):
    """One assignment of a role with its holder (USR-17). `total` of the list counts
    assignments, not distinct users: a user can appear once per org and once at app scope."""

    user_id: uuid.UUID
    email: str
    display_name: str | None
    user_status: UserStatus = Field(description="Account status; disabled users are listed.")
    active: bool = Field(
        description=(
            "Not expired (database clock); ignores user, org and app status, "
            "so not 'effective in tokens'."
        )
    )


class OrgMemberRead(BaseModel):
    """One org-scoped assignment with its holder (USR-23). `total` of the list counts
    assignments, not distinct users: a user with two roles in the org appears twice."""

    assignment_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: str | None
    user_status: UserStatus = Field(description="Account status; disabled users are listed.")
    app: str  # app key
    role_id: uuid.UUID
    role_key: str
    role_name: str
    granted_by: uuid.UUID | None
    created_at: datetime = Field(
        description=(
            "When the assignment was first granted; kept when an expired assignment is "
            "re-granted in place."
        )
    )
    expires_at: datetime | None
    active: bool = Field(
        description=(
            "Not expired (database clock); ignores user, org and app status, "
            "so not 'effective in tokens'."
        )
    )


class ManifestPermission(BaseModel):
    """One declared permission. `scope` is required on `org_mode = required` apps unless the
    key is reserved (ADR-0015); an omitted `description` leaves the stored one."""

    model_config = ConfigDict(extra="forbid")

    key: PermissionKey
    description: Description | None = None
    is_admin: bool = False
    scope: PermissionScope | None = None


class ManifestRole(BaseModel):
    """An extra role; additive (permissions are only ever added to an existing role)."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=ROLE_KEY_RE, max_length=160)
    name: RoleName
    permissions: list[PermissionKey] = Field(max_length=200)


class ManifestMember(BaseModel):
    """Narrows `<app>:member` to these permissions (never widens past the eligible set)."""

    model_config = ConfigDict(extra="forbid")

    permissions: list[PermissionKey] = Field(max_length=200)


class ManifestPut(BaseModel):
    """The app's permission manifest (ADR-0001 §6, ADR-0016 §6)."""

    model_config = ConfigDict(extra="forbid")

    permissions: list[ManifestPermission] = Field(max_length=200)
    roles: list[ManifestRole] = Field(default_factory=list, max_length=50)
    member: ManifestMember | None = None


class ManifestNotDeclared(BaseModel):
    key: str
    held_by_roles: list[str]


class ManifestPermissionsResult(BaseModel):
    added: list[str]
    description_updated: list[str]
    not_in_manifest: list[ManifestNotDeclared]


class ManifestRolesResult(BaseModel):
    added: list[str]
    updated: list[str]


class ManifestMemberResult(BaseModel):
    role_key: str
    rebuilt: bool
    added: list[str]
    removed: list[str]


class ManifestResult(BaseModel):
    app: str
    changed: bool
    permissions: ManifestPermissionsResult
    roles: ManifestRolesResult
    member: ManifestMemberResult
