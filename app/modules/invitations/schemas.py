import uuid
from datetime import datetime
from enum import StrEnum

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


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class OrgInvitationRead(BaseModel):
    """Staff view of one invitation (USR-23). No invitee email, token, token hash or email HMAC."""

    id: uuid.UUID
    app: str  # app key
    role_id: uuid.UUID
    role_key: str
    invited_by: uuid.UUID | None
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    accepted_by: uuid.UUID | None
    revoked_at: datetime | None
    status: InvitationStatus = Field(
        description=(
            "Computed at read time on the database clock: accepted > revoked > expired > "
            "pending. An invitation can read pending while its inviter has since lost the "
            "right to invite; accepting it is then refused."
        )
    )
