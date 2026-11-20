"""adm033 app releases

Revision ID: 89fd8d667a25
Revises: c4e8a2f7b916
Create Date: 2026-11-20 06:59:16.992495

ADM-033: release log. RESTRICT on `apps` keeps history. `downgrade()` drops the table (and its
records).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "89fd8d667a25"
down_revision: str | Sequence[str] | None = "c4e8a2f7b916"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "app_releases",
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=10), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("git_sha", sa.String(length=40), nullable=True),
        sa.Column(
            "images",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "changes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("deployment_pr", sa.String(length=255), nullable=True),
        sa.Column("has_migrations", sa.Boolean(), nullable=True),
        sa.Column("deploy_run_url", sa.String(length=255), nullable=True),
        sa.Column("created_via", sa.String(length=16), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(environment = 'production' AND version ~ '^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$') OR (environment = 'staging' AND version ~ '^sha-[0-9a-f]{7}$')",
            name=op.f("ck_app_releases_version_matches_environment"),
        ),
        sa.CheckConstraint(
            "(status = 'deployed') = (deployed_at IS NOT NULL)",
            name=op.f("ck_app_releases_deployed_iff_deployed_at"),
        ),
        sa.CheckConstraint(
            "CASE WHEN jsonb_typeof(changes) = 'array' THEN jsonb_array_length(changes) <= 200 ELSE false END",
            name=op.f("ck_app_releases_changes_bounded"),
        ),
        sa.CheckConstraint(
            "CASE WHEN jsonb_typeof(images) = 'array' THEN jsonb_array_length(images) <= 10 ELSE false END",
            name=op.f("ck_app_releases_images_bounded"),
        ),
        sa.CheckConstraint(
            "created_via IN ('app', 'deployer')", name=op.f("ck_app_releases_created_via_valid")
        ),
        sa.CheckConstraint(
            "deploy_run_url IS NULL OR deploy_run_url ~ '^https://github\\.com/mmlabs-online/deployment/actions/runs/[0-9]+(/attempts/[0-9]+)?$'",
            name=op.f("ck_app_releases_deploy_run_url_format"),
        ),
        sa.CheckConstraint(
            "deployment_pr IS NULL OR deployment_pr ~ '^https://github\\.com/mmlabs-online/deployment/pull/[0-9]+$'",
            name=op.f("ck_app_releases_deployment_pr_format"),
        ),
        sa.CheckConstraint(
            "environment <> 'staging' OR git_sha IS NULL OR version = ('sha-' || left(git_sha, 7))",
            name=op.f("ck_app_releases_staging_version_matches_sha"),
        ),
        sa.CheckConstraint(
            "environment IN ('production', 'staging')",
            name=op.f("ck_app_releases_environment_valid"),
        ),
        sa.CheckConstraint(
            "git_sha IS NULL OR git_sha ~ '^[0-9a-f]{40}$'",
            name=op.f("ck_app_releases_git_sha_format"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'deployed', 'failed')", name=op.f("ck_app_releases_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["app_id"], ["apps.id"], name="fk_app_releases_app_id_apps", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_releases")),
        sa.UniqueConstraint(
            "app_id", "environment", "version", name="uq_app_releases_app_id_environment_version"
        ),
    )
    op.create_index(
        "ix_app_releases_app_id_created_at_id",
        "app_releases",
        ["app_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_app_releases_created_at_id", "app_releases", ["created_at", "id"], unique=False
    )
    op.create_index(
        "ix_app_releases_live",
        "app_releases",
        ["app_id", "environment", "deployed_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'deployed'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_app_releases_live",
        table_name="app_releases",
        postgresql_where=sa.text("status = 'deployed'"),
    )
    op.drop_index("ix_app_releases_created_at_id", table_name="app_releases")
    op.drop_index("ix_app_releases_app_id_created_at_id", table_name="app_releases")
    op.drop_table("app_releases")
