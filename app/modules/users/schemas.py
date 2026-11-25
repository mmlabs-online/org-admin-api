import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.core.text import UserDisplayName
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
