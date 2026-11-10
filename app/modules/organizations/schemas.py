import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.organizations.models import OrganizationStatus

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"


class OrganizationCreate(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN, max_length=63)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    status: OrganizationStatus | None = None


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime
