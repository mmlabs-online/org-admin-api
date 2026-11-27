import uuid
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.text import UserDisplayName
from app.modules.rbac.models import APP_KEY_RE, AppStatus
from app.modules.users.models import UserStatus


class UserRead(BaseModel):
    """Admin view of a user. Explicit fields only: never `password_hash` (R11)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    status: UserStatus
    email_verified_at: datetime | None
    display_name: str | None
    created_at: datetime
    updated_at: datetime


class UserInclude(StrEnum):
    ACCESS = "access"


class EmailMatch(StrEnum):
    EXACT = "exact"
    CONTAINS = "contains"


class UserSearch(BaseModel):
    """Body of `POST /users/search`: the `GET /users` filters, kept out of the URL (D43)."""

    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(None, min_length=1, max_length=254)
    email_match: EmailMatch | None = Field(
        None, description="`exact` or `contains` (default). Requires `email`."
    )
    status: UserStatus | None = None
    app: str | None = Field(None, pattern=APP_KEY_RE, description="App key filter.")
    role_id: uuid.UUID | None = Field(None, description="Role id filter.")
    include: list[UserInclude] | None = Field(None, description="Opt-in extras; only `access`.")
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)


class AccessRole(BaseModel):
    assignment_id: uuid.UUID
    role_id: uuid.UUID
    role_key: str
    org_id: uuid.UUID | None = Field(description="Null for an app-scope assignment.")
    expires_at: datetime | None = Field(description="Null when the assignment does not expire.")


class AppAccess(BaseModel):
    app: str = Field(description="App key.")
    app_status: AppStatus
    roles: list[AccessRole]


class UserWithAccessRead(UserRead):
    """`UserRead` plus the active-access summary (`include=access`, USR-34)."""

    access: list[AppAccess] = Field(
        description="Active role assignments grouped by app key; at most 20 in total."
    )
    access_truncated: bool = Field(description="True when the user has more than 20 active ones.")


class UserUpdate(BaseModel):
    """Staff partial update. Unlike `OrganizationUpdate`, explicit `status: null` is rejected (422;
    USR-01b AC). An explicit `display_name: null` clears the name; `""` is a 422 (USR-33)."""

    model_config = ConfigDict(extra="forbid")

    status: UserStatus | None = None
    display_name: UserDisplayName | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status must not be null")
        return self


class DisplayNameSet(BaseModel):
    """Self-service body: both fields required; `display_name: null` clears the name."""

    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    display_name: UserDisplayName | None
