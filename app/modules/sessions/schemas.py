import uuid
from datetime import datetime

from pydantic import BaseModel, Field

_FIRST_SIGN_IN = "Recorded when this browser first signed in; shared by every account on it."


class UserSessionRead(BaseModel):
    """An active account session of a user, for staff (explicit fields only; USR-10)."""

    id: uuid.UUID = Field(
        description="Account session id; the `account_id` apps send to /auth/token."
    )
    created_at: datetime
    last_used_at: datetime = Field(description="Last sign-in of this account on this device.")
    device_last_seen_at: datetime = Field(
        description="Browser last seen; refreshed at most hourly."
    )
    ip: str | None = Field(description=_FIRST_SIGN_IN)
    user_agent: str | None = Field(description=_FIRST_SIGN_IN)
