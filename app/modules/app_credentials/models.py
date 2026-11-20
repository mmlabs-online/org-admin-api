"""App credentials (USR-05a, ADR-0016): per-app API keys, stored only as a SHA-256 hash.

CHECK texts and the partial-index `postgresql_where` text must match the migration exactly:
Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPkMixin


class AppCredentialScope(StrEnum):
    """One scope per key (ADR-0021 §1). ADM-042 widens the CHECK with the runtime scopes."""

    MANIFEST = "manifest"
    RELEASES = "releases"


class AppCredential(UUIDPkMixin, Base):
    """One credential of exactly one app. Active = `revoked_at IS NULL`."""

    __tablename__ = "app_credentials"
    __table_args__ = (
        CheckConstraint("key_id ~ '^[0-9a-f]{16}$'", name="key_id_format"),
        CheckConstraint("octet_length(secret_hash) = 32", name="secret_hash_length"),
        # ADM-042 drops and recreates `ck_app_credentials_scope_valid` to add the runtime scopes.
        CheckConstraint("scope IN ('manifest', 'releases')", name="scope_valid"),
        Index("ix_app_credentials_app_id_created_at", "app_id", "created_at"),
        Index(
            "ix_app_credentials_app_id_scope_active",
            "app_id",
            "scope",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE", name="fk_app_credentials_app_id_apps"),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'manifest'"),
        default=AppCredentialScope.MANIFEST.value,
    )
    key_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_app_credentials_created_by_users")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # never include the hash (R11)
        return f"<AppCredential id={self.id} key_id={self.key_id}>"
