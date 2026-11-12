"""Device and account sessions (identity-architecture.md §3, ADR-0004).

CHECK texts must match the migration exactly: Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPkMixin


class DeviceSession(UUIDPkMixin, Base):
    """One browser (cookie `mm_device`); only SHA-256 hashes of the cookie are stored (R11)."""

    __tablename__ = "device_sessions"
    __table_args__ = (
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("(prev_token_hash IS NULL) = (prev_valid_until IS NULL)", name="prev_pair"),
        Index(
            "ix_device_sessions_prev_token_hash",
            "prev_token_hash",
            postgresql_where=text("prev_token_hash IS NOT NULL"),
        ),
    )

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    prev_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    prev_valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))

    def __repr__(self) -> str:  # never include hashes (R11)
        return f"<DeviceSession id={self.id}>"


class AccountSession(UUIDPkMixin, Base):
    """An account signed in on a device; its id is the `account_id` apps use (ADR-0004)."""

    __tablename__ = "account_sessions"
    __table_args__ = (
        Index(
            "uq_account_sessions_active",
            "device_session_id",
            "user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_account_sessions_user_id", "user_id"),
    )

    device_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("device_sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
