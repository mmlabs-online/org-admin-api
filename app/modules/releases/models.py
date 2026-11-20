"""Release log (ADM-033, ADR-0021).

CHECK texts and the partial-index `postgresql_where` text must match the migration exactly:
Alembic does not compare CHECKs. The DB CHECKs are a little more lenient than the write
validation of ADM-034/036, which must be at least as strict.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class ReleaseEnvironment(StrEnum):
    PRODUCTION = "production"
    STAGING = "staging"


class ReleaseStatus(StrEnum):
    PENDING = "pending"
    DEPLOYED = "deployed"
    FAILED = "failed"


class ReleaseCreatedVia(StrEnum):
    APP = "app"  # the app's release workflow (ADM-034)
    DEPLOYER = "deployer"  # created from a deploy report (ADM-036)


PRODUCTION_VERSION_RE = r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
STAGING_VERSION_RE = r"^sha-[0-9a-f]{7}$"
GIT_SHA_RE = r"^[0-9a-f]{40}$"
DEPLOYMENT_PR_RE = r"^https://github\.com/mmlabs-online/deployment/pull/[0-9]+$"
DEPLOY_RUN_URL_RE = (
    r"^https://github\.com/mmlabs-online/deployment/actions/runs/[0-9]+(/attempts/[0-9]+)?$"
)

MAX_IMAGES = 10
MAX_CHANGES = 200


class AppRelease(UUIDPkMixin, TimestampMixin, Base):
    """One release of an app in one environment. `deployed_at` is the latest deploy."""

    __tablename__ = "app_releases"
    __table_args__ = (
        CheckConstraint("environment IN ('production', 'staging')", name="environment_valid"),
        CheckConstraint("status IN ('pending', 'deployed', 'failed')", name="status_valid"),
        CheckConstraint("created_via IN ('app', 'deployer')", name="created_via_valid"),
        CheckConstraint(
            f"(environment = 'production' AND version ~ '{PRODUCTION_VERSION_RE}') "
            f"OR (environment = 'staging' AND version ~ '{STAGING_VERSION_RE}')",
            name="version_matches_environment",
        ),
        CheckConstraint(f"git_sha IS NULL OR git_sha ~ '{GIT_SHA_RE}'", name="git_sha_format"),
        CheckConstraint(
            "environment <> 'staging' OR git_sha IS NULL OR version = ('sha-' || left(git_sha, 7))",
            name="staging_version_matches_sha",
        ),
        CheckConstraint(
            "(status = 'deployed') = (deployed_at IS NOT NULL)", name="deployed_iff_deployed_at"
        ),
        CheckConstraint(
            "CASE WHEN jsonb_typeof(images) = 'array' "
            f"THEN jsonb_array_length(images) <= {MAX_IMAGES} ELSE false END",
            name="images_bounded",
        ),
        CheckConstraint(
            "CASE WHEN jsonb_typeof(changes) = 'array' "
            f"THEN jsonb_array_length(changes) <= {MAX_CHANGES} ELSE false END",
            name="changes_bounded",
        ),
        CheckConstraint(
            f"deployment_pr IS NULL OR deployment_pr ~ '{DEPLOYMENT_PR_RE}'",
            name="deployment_pr_format",
        ),
        CheckConstraint(
            f"deploy_run_url IS NULL OR deploy_run_url ~ '{DEPLOY_RUN_URL_RE}'",
            name="deploy_run_url_format",
        ),
        UniqueConstraint(
            "app_id", "environment", "version", name="uq_app_releases_app_id_environment_version"
        ),
        Index("ix_app_releases_created_at_id", "created_at", "id"),
        Index("ix_app_releases_app_id_created_at_id", "app_id", "created_at", "id"),
        Index(
            "ix_app_releases_live",
            "app_id",
            "environment",
            "deployed_at",
            "id",
            postgresql_where=text("status = 'deployed'"),
        ),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("apps.id", ondelete="RESTRICT", name="fk_app_releases_app_id_apps"),
        nullable=False,
    )
    environment: Mapped[str] = mapped_column(String(10), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default=text("'pending'"),
        default=ReleaseStatus.PENDING.value,
    )
    git_sha: Mapped[str | None] = mapped_column(String(40))
    images: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    changes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    deployment_pr: Mapped[str | None] = mapped_column(String(255))
    has_migrations: Mapped[bool | None] = mapped_column(Boolean)
    deploy_run_url: Mapped[str | None] = mapped_column(String(255))
    created_via: Mapped[str] = mapped_column(String(16), nullable=False)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<AppRelease id={self.id} {self.environment} {self.version} {self.status}>"
