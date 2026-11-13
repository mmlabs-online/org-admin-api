"""Per-email login failure counters (D11, ADR-0002 §5) and email tokens/send log (USR-04).

CHECK texts must match the migration exactly: Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPkMixin


class LoginFailure(Base):
    """Keyed by HMAC-SHA256(IDENTITY_HMAC_KEY, normalized email); no email is stored (R11)."""

    __tablename__ = "login_failures"
    __table_args__ = (
        CheckConstraint("octet_length(email_hmac) = 32", name="email_hmac_length"),
        CheckConstraint("failures >= 1", name="failures_positive"),
    )

    email_hmac: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False)
    last_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailToken(UUIDPkMixin, Base):
    """One-time email link token; only SHA-256 of the token is stored (R11, ADR-0007).

    `used_at` is set when the token is consumed or superseded by a newer one.
    """

    __tablename__ = "email_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('verify', 'reset')", name="purpose_valid"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index(
            "ix_email_tokens_user_id_purpose_active",
            "user_id",
            "purpose",
            postgresql_where=text("used_at IS NULL"),
        ),
        Index("ix_email_tokens_created_at", "created_at"),  # daily cap count, retention
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(10), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # never include the hash (R11)
        return f"<EmailToken id={self.id} purpose={self.purpose}>"


class EmailSendLog(UUIDPkMixin, Base):
    """One accepted verification/reset send, keyed by the email HMAC (no email stored, R11)."""

    __tablename__ = "email_send_log"
    __table_args__ = (
        CheckConstraint("octet_length(email_hmac) = 32", name="email_hmac_length"),
        CheckConstraint("purpose IN ('verify', 'reset')", name="purpose_valid"),
        Index("ix_email_send_log_email_hmac_purpose_sent_at", "email_hmac", "purpose", "sent_at"),
        Index("ix_email_send_log_sent_at", "sent_at"),
    )

    email_hmac: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    purpose: Mapped[str] = mapped_column(String(10), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
