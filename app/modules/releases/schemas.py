import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.modules.releases.models import ReleaseCreatedVia, ReleaseEnvironment, ReleaseStatus

__all__ = [
    "ChangeRisk",
    "ReleaseChange",
    "ReleaseCreatedVia",
    "ReleaseDetail",
    "ReleaseEnvironment",
    "ReleaseImage",
    "ReleaseSort",
    "ReleaseStatus",
    "ReleaseSummary",
]

_FROM_REPORT = "null for a record created from a deploy report."


class ChangeRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReleaseSort(StrEnum):
    CREATED_AT = "created_at"
    DEPLOYED_AT = "deployed_at"


class ReleaseImage(BaseModel):
    name: str
    digest: str = Field(description='Image digest, "sha256:<64 hex>".')


class ReleaseChange(BaseModel):
    pr_url: str
    title: str = Field(
        description="Untrusted plain text (a PR title) supplied by CI, returned verbatim. "
        "Render it as text, never as HTML."
    )
    ticket_id: str | None = None
    risk: ChangeRisk | None = None


class ReleaseSummary(BaseModel):
    id: uuid.UUID
    app_key: str
    environment: ReleaseEnvironment
    version: str
    status: ReleaseStatus
    git_sha: str | None = Field(description=f"Full commit sha; {_FROM_REPORT}")
    deployment_pr: str | None = Field(description=f"Deployment PR URL; {_FROM_REPORT}")
    has_migrations: bool | None = Field(description=f"Null means unknown: {_FROM_REPORT}")
    deploy_run_url: str | None
    created_via: ReleaseCreatedVia
    changes_count: int
    created_at: datetime
    updated_at: datetime
    deployed_at: datetime | None = Field(
        description="Latest deploy of this version; overwritten on redeploy or rollback; "
        "null unless `status = deployed`."
    )


class ReleaseDetail(ReleaseSummary):
    images: list[ReleaseImage]
    changes: list[ReleaseChange]
