"""Deployer credentials (ADM-035, ADR-0021 §2): release-report keys, stored only as a SHA-256 hash.

Not bound to an app. CHECK texts must match the migration exactly: Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, LargeBinary, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPkMixin


class DeployerCredential(UUIDPkMixin, Base):
    """One deployer key. Active = `revoked_at IS NULL`; at most 2 active (service rule)."""

    __tablename__ = "deployer_credentials"
    __table_args__ = (
        CheckConstraint("key_id ~ '^[0-9a-f]{16}$'", name="key_id_format"),
        CheckConstraint("octet_length(secret_hash) = 32", name="secret_hash_length"),
    )

    key_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_deployer_credentials_created_by_users")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # never include the hash (R11)
        return f"<DeployerCredential id={self.id} key_id={self.key_id}>"
