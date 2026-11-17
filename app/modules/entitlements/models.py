"""Org product entitlements (ADM-006).

CHECK texts, FK names and server defaults must match the migration exactly: Alembic does not
compare CHECKs.
"""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class OrgEntitlement(UUIDPkMixin, TimestampMixin, Base):
    """The right of an organization to use an app, with a plan label and limits (no billing)."""

    __tablename__ = "org_entitlements"
    __table_args__ = (
        UniqueConstraint("org_id", "app_id", name="uq_org_entitlements_org_id_app_id"),
        CheckConstraint("plan ~ '^[a-z0-9][a-z0-9_-]{0,62}$'", name="plan_format"),
        CheckConstraint("jsonb_typeof(limits) = 'object'", name="limits_object"),
        Index("ix_org_entitlements_app_id", "app_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "organizations.id", ondelete="RESTRICT", name="fk_org_entitlements_org_id_organizations"
        ),
        nullable=False,
    )
    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("apps.id", ondelete="RESTRICT", name="fk_org_entitlements_app_id_apps"),
        nullable=False,
    )
    plan: Mapped[str] = mapped_column(String(63), nullable=False)
    limits: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    def __repr__(self) -> str:
        return f"<OrgEntitlement id={self.id} org={self.org_id} app={self.app_id}>"
