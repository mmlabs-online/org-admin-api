import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

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


class UserStatusUpdate(BaseModel):
    """Unlike `OrganizationUpdate`, explicit null is rejected (422).

    Intentional, per USR-01b AC.
    """

    model_config = ConfigDict(extra="forbid")

    status: UserStatus
