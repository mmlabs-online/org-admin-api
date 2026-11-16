import uuid

from pydantic import BaseModel, ConfigDict, Field


class MePermissions(BaseModel):
    """The caller's own admin authorization (USR-11, ADR-0008 amendment). A UI hint only:
    the API enforces every permission. `admin:*` is returned unexpanded and matches every
    `admin:` key (same rule as `permits()`). Additive within v1: new fields may appear."""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    roles: list[str] = Field(description="App-scope `admin` role keys, sorted")
    permissions: list[str] = Field(
        description="Effective `admin:` permission keys, sorted; `admin:*` unexpanded"
    )
