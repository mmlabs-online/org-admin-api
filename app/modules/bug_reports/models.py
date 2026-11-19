"""End-user bug reports (ADM-028).

CHECK texts and the partial-index `postgresql_where` texts must match the migration exactly:
Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class BugReportStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class BugReport(UUIDPkMixin, TimestampMixin, Base):
    """A bug report filed by a member of an app. Done iff `handled_at` is set."""

    __tablename__ = "bug_reports"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'done')", name="status_valid"),
        CheckConstraint("(status = 'done') = (handled_at IS NOT NULL)", name="handled_iff_done"),
        CheckConstraint("handled_by IS NULL OR status = 'done'", name="handler_only_when_done"),
        CheckConstraint("char_length(description) BETWEEN 1 AND 5000", name="description_length"),
        CheckConstraint(
            "resolution_note IS NULL OR char_length(resolution_note) BETWEEN 1 AND 5000",
            name="resolution_note_length",
        ),
        CheckConstraint(
            "page_url IS NULL OR page_url ~ '^https?://[^/?#@[:space:]]+/[^?#[:space:]]*$'",
            name="page_url_reduced",
        ),
        Index("ix_bug_reports_created_at_id", "created_at", "id"),
        Index("ix_bug_reports_app_id_status_created_at_id", "app_id", "status", "created_at", "id"),
        Index("ix_bug_reports_user_id_created_at", "user_id", "created_at"),
        Index(
            "ix_bug_reports_open_user_id_app_id",
            "user_id",
            "app_id",
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_bug_reports_org_id", "org_id", postgresql_where=text("org_id IS NOT NULL")),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("apps.id", ondelete="RESTRICT", name="fk_bug_reports_app_id_apps"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_bug_reports_user_id_users"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "organizations.id", ondelete="SET NULL", name="fk_bug_reports_org_id_organizations"
        )
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    page_url: Mapped[str | None] = mapped_column(String(2048))
    app_version: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'open'"),
        default=BugReportStatus.OPEN.value,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)
    handled_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_bug_reports_handled_by_users")
    )
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<BugReport id={self.id} status={self.status}>"
