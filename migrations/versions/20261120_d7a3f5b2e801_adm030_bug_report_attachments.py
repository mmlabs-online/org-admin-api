"""adm030 bug report attachments

Revision ID: d7a3f5b2e801
Revises: 8c2f6a1d4e93
Create Date: 2026-11-20 09:06:48.313799

ADM-030: expand-only. New table `bug_report_attachments` (metadata of images stored in Drive).
`drive_file_id` is never exposed by the API; `file_deleted_at` is the spec's deleted marker (set
by ADM-031). Rows cascade with their report.

`downgrade()` drops the table (index and constraints go with it; attachment metadata is lost,
the Drive files stay and are found by the orphan sweep).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7a3f5b2e801"
down_revision: str | Sequence[str] | None = "8c2f6a1d4e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "bug_report_attachments",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("drive_file_id", sa.String(length=128), nullable=False),
        sa.Column("content_type", sa.String(length=20), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "content_type IN ('image/png', 'image/jpeg', 'image/webp', 'image/gif')",
            name=op.f("ck_bug_report_attachments_content_type_valid"),
        ),
        sa.CheckConstraint(
            "drive_file_id ~ '^[A-Za-z0-9_-]{1,128}$'",
            name=op.f("ck_bug_report_attachments_drive_file_id_format"),
        ),
        sa.CheckConstraint(
            "size_bytes BETWEEN 12 AND 5242880",
            name=op.f("ck_bug_report_attachments_size_bytes_range"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["bug_reports.id"],
            name="fk_bug_report_attachments_report_id_bug_reports",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bug_report_attachments")),
        sa.UniqueConstraint("drive_file_id", name=op.f("uq_bug_report_attachments_drive_file_id")),
    )
    op.create_index(
        "ix_bug_report_attachments_report_id_created_at",
        "bug_report_attachments",
        ["report_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_bug_report_attachments_report_id_created_at", table_name="bug_report_attachments"
    )
    op.drop_table("bug_report_attachments")
