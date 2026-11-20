import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.app_credentials.models import AppCredentialScope


class AppCredentialRead(BaseModel):
    """Metadata only: never the secret, never the hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app: str
    key_id: str
    scope: AppCredentialScope
    created_by: uuid.UUID | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    active: bool


class AppCredentialIssued(AppCredentialRead):
    credential: str = Field(repr=False)


class AppCredentialIssue(BaseModel):
    """Optional issuance body; no body or `{}` means `manifest` (current clients keep working)."""

    model_config = ConfigDict(extra="forbid")

    scope: AppCredentialScope = AppCredentialScope.MANIFEST
