"""Org invitations (USR-06b, ADR-0014).

CHECK texts and the partial-index `postgresql_where` text must match the migration exactly:
Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPkMixin


class OrgInvitation(UUIDPkMixin, Base):
    """One invitation of an email address into an org with a role of one app.

    Only `SHA-256(token)` and the email HMAC are stored (R11): no email, no token. Pending =
    neither accepted nor revoked; expiry is checked against the database clock.
    """

    __tablename__ = "org_invitations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["role_id", "app_id"],
            ["roles.id", "roles.app_id"],
            ondelete="CASCADE",
            name="fk_org_invitations_role_same_app",
        ),
        CheckConstraint("octet_length(email_hmac) = 32", name="email_hmac_length"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL", name="not_accepted_and_revoked"
        ),
        Index(
            "uq_org_invitations_pending",
            "org_id",
            "app_id",
            "email_hmac",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_org_invitations_org_id_created_at", "org_id", "created_at"),
        Index("ix_org_invitations_invited_by_created_at", "invited_by", "created_at"),
        Index("ix_org_invitations_role_id_app_id", "role_id", "app_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "organizations.id", ondelete="CASCADE", name="fk_org_invitations_org_id_organizations"
        ),
        nullable=False,
    )
    app_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    email_hmac: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_org_invitations_invited_by_users")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_org_invitations_accepted_by_users")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # never include the hashes (R11)
        return f"<OrgInvitation id={self.id} org_id={self.org_id}>"
