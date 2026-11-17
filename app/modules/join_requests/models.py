"""Join requests for `approval` apps (USR-03g).

CHECK texts and the partial-index `postgresql_where` text must match the migration exactly:
Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPkMixin


class JoinRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AppJoinRequest(UUIDPkMixin, Base):
    """A user's request to join an `approval` app. Pending iff `decided_at` is NULL."""

    __tablename__ = "join_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)", name="decided_iff_not_pending"
        ),
        Index(
            "uq_join_requests_pending",
            "app_id",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_join_requests_app_id_status_created_at_id", "app_id", "status", "created_at", "id"
        ),
        Index("ix_join_requests_user_id_app_id_created_at", "user_id", "app_id", "created_at"),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE", name="fk_join_requests_app_id_apps"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_join_requests_user_id_users"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_join_requests_decided_by_users")
    )

    def __repr__(self) -> str:
        return f"<AppJoinRequest id={self.id} status={self.status}>"
