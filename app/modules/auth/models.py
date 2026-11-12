"""Per-email login failure counters (D11, ADR-0002 §5).

CHECK texts must match the migration exactly: Alembic does not compare CHECKs.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, LargeBinary, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


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
