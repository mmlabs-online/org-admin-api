import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.organizations.models import OrganizationStatus
from app.modules.users.models import UserStatus

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"

OrganizationSort = Literal["created_at", "-created_at", "name", "-name", "slug", "-slug"]
DEFAULT_ORGANIZATION_SORT: OrganizationSort = "-created_at"


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
    created_by: uuid.UUID | None = None  # additive (USR-06a); NULL for staff-created orgs


class AuthOrgCreate(BaseModel):
    """End-user org creation (`POST /api/v1/auth/orgs`)."""

    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    slug: str = Field(pattern=SLUG_PATTERN, max_length=63)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)


class AuthOrgRead(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime
    roles: list[str]  # the caller's role keys in this org


OrgAccess = Literal["member", "super_admin"]


class MyOrgRead(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    status: OrganizationStatus
    roles: list[str]  # the caller's role keys in this org
    access: OrgAccess = Field(
        description=(
            "member: the caller holds roles in the org; super_admin: listed through the app's "
            "super-admin role (ADR-0030), roles is empty; tolerate unknown values"
        )
    )


class AppOrgMemberRead(BaseModel):
    user_id: uuid.UUID  # = access-token `sub`
    email: str | None  # null for a disabled account
    email_verified: bool
    display_name: str | None  # null when unset or for a disabled account
    status: UserStatus
    org_roles: list[str]  # full role keys of the app held in the org, not expired, sorted
    joined_at: datetime  # earliest creation among those assignments


class AppOrgInvitationRead(BaseModel):
    id: uuid.UUID
    role: str  # full role key of the app, e.g. "simap:member"
    created_at: datetime
    expires_at: datetime


class OrgMemberRemove(BaseModel):
    """`POST /auth/orgs/{org_id}/members/{user_id}/remove`: `account_id` is the caller's."""

    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
