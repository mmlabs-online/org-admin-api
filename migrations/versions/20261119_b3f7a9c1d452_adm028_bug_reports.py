"""adm028 bug reports

Revision ID: b3f7a9c1d452
Revises: d9e4b2a61f73
Create Date: 2026-11-19 10:33:13.519098

ADM-028: end-user bug reports (text only; attachments are ADM-030). RESTRICT on `apps` and
`users` keeps reports; `org_id` and `handled_by` go to NULL. `downgrade()` drops the table (and
its reports).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f7a9c1d452"
down_revision: str | Sequence[str] | None = "d9e4b2a61f73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "bug_reports",
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("page_url", sa.String(length=2048), nullable=True),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'open'"), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("handled_by", sa.Uuid(), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
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
            "(status = 'done') = (handled_at IS NOT NULL)",
            name=op.f("ck_bug_reports_handled_iff_done"),
        ),
        sa.CheckConstraint(
            "handled_by IS NULL OR status = 'done'",
            name=op.f("ck_bug_reports_handler_only_when_done"),
        ),
        sa.CheckConstraint(
            "page_url IS NULL OR page_url ~ '^https?://[^/?#@[:space:]]+/[^?#[:space:]]*$'",
            name=op.f("ck_bug_reports_page_url_reduced"),
        ),
        sa.CheckConstraint("status IN ('open', 'done')", name=op.f("ck_bug_reports_status_valid")),
        sa.CheckConstraint(
            "char_length(description) BETWEEN 1 AND 5000",
            name=op.f("ck_bug_reports_description_length"),
        ),
        sa.CheckConstraint(
            "resolution_note IS NULL OR char_length(resolution_note) BETWEEN 1 AND 5000",
            name=op.f("ck_bug_reports_resolution_note_length"),
        ),
        sa.ForeignKeyConstraint(
            ["app_id"], ["apps.id"], name="fk_bug_reports_app_id_apps", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["handled_by"],
            ["users.id"],
            name="fk_bug_reports_handled_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_bug_reports_org_id_organizations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_bug_reports_user_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bug_reports")),
    )
    op.create_index(
        "ix_bug_reports_app_id_status_created_at_id",
        "bug_reports",
        ["app_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_bug_reports_created_at_id", "bug_reports", ["created_at", "id"], unique=False
    )
    op.create_index(
        "ix_bug_reports_open_user_id_app_id",
        "bug_reports",
        ["user_id", "app_id"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_bug_reports_org_id",
        "bug_reports",
        ["org_id"],
        unique=False,
        postgresql_where=sa.text("org_id IS NOT NULL"),
    )
    op.create_index(
        "ix_bug_reports_user_id_created_at", "bug_reports", ["user_id", "created_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_bug_reports_user_id_created_at", table_name="bug_reports")
    op.drop_index(
        "ix_bug_reports_org_id",
        table_name="bug_reports",
        postgresql_where=sa.text("org_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_bug_reports_open_user_id_app_id",
        table_name="bug_reports",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_index("ix_bug_reports_created_at_id", table_name="bug_reports")
    op.drop_index("ix_bug_reports_app_id_status_created_at_id", table_name="bug_reports")
    op.drop_table("bug_reports")
