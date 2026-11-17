import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    email: EmailStr
    role: str = Field(min_length=1, max_length=100)


class InvitationCreated(BaseModel):
    """Identical whatever the target email is (no account/member oracle); never echoes it."""

    id: uuid.UUID
    org_id: uuid.UUID
    role: str
    expires_at: datetime


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    token: str = Field(min_length=1, max_length=256)


class InvitationAccepted(BaseModel):
    org_id: uuid.UUID
    roles: list[str]  # the caller's role keys in this org
