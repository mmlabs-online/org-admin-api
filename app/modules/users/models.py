from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class User(UUIDPkMixin, TimestampMixin, Base):
    """An mmlabs Identity account (one account for every mmlabs app)."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),)

    email: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=UserStatus.ACTIVE.value,
        server_default=UserStatus.ACTIVE.value,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str | None] = mapped_column(String(100))

    def __repr__(self) -> str:  # never include password_hash (R11)
        return f"<User id={self.id} status={self.status}>"
