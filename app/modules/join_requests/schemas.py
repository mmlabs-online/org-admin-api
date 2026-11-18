import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

JoinRequestStatusFilter = Literal["pending", "approved", "rejected", "cancelled"]


class JoinRequestRead(BaseModel):
    id: uuid.UUID
    app_key: str
    user_id: uuid.UUID
    email: str
    display_name: str | None  # untrusted user text: render as text only
    email_verified: bool
    user_status: str
    status: str
    created_at: datetime
    decided_at: datetime | None
    decided_by: uuid.UUID | None
