import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AppCredentialRead(BaseModel):
    """Metadata only: never the secret, never the hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app: str
    key_id: str
    created_by: uuid.UUID | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    active: bool


class AppCredentialIssued(AppCredentialRead):
    credential: str
