"""RBAC catalog for every mmlabs app (ADR-0001 §3-§4, ADR-0003).

`apps` and `roles` reference each other (`apps.default_role_id`), so all five tables live here.
CHECK texts must match the migration exactly: Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin

APP_KEY_RE = r"^[a-z][a-z0-9-]{1,31}$"
PERMISSION_KEY_RE = r"^[a-z][a-z0-9-]{1,31}:([a-z][a-z0-9-]{0,47}:[a-z][a-z0-9-]{0,47}|\*)$"
ROLE_KEY_RE = r"^[a-z][a-z0-9-]{1,31}:[a-z][a-z0-9-]{0,47}$"


class AppStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class JoinPolicy(StrEnum):
    OPEN = "open"
    APPROVAL = "approval"
    CLOSED = "closed"


class OrgMode(StrEnum):
    NONE = "none"
    REQUIRED = "required"


class App(UUIDPkMixin, TimestampMixin, Base):
    """A registered mmlabs app (the admin API itself is app `admin`)."""

    __tablename__ = "apps"
    __table_args__ = (
        UniqueConstraint("key", name="uq_apps_key"),
        CheckConstraint(f"key ~ '{APP_KEY_RE}'", name="key_format"),
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("join_policy IN ('open', 'approval', 'closed')", name="join_policy_valid"),
        CheckConstraint("org_mode IN ('none', 'required')", name="org_mode_valid"),
        ForeignKeyConstraint(
            ["default_role_id", "id"],
            ["roles.id", "roles.app_id"],
            name="fk_apps_default_role_same_app",
            use_alter=True,
        ),
    )

    key: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AppStatus.ACTIVE.value,
        server_default=AppStatus.ACTIVE.value,
    )
    join_policy: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=JoinPolicy.OPEN.value,
        server_default=JoinPolicy.OPEN.value,
    )
    org_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OrgMode.NONE.value,
        server_default=OrgMode.NONE.value,
    )
    origins: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    default_role_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class Permission(UUIDPkMixin, TimestampMixin, Base):
    """A namespaced permission `<app>:<resource>:<action>` or the wildcard `<app>:*`."""

    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("app_id", "key", name="uq_permissions_app_id_key"),
        UniqueConstraint("id", "app_id", name="uq_permissions_id_app_id"),
        CheckConstraint(f"key ~ '{PERMISSION_KEY_RE}'", name="key_format"),
        CheckConstraint("is_admin OR key NOT LIKE '%:*'", name="wildcard_is_admin"),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class Role(UUIDPkMixin, TimestampMixin, Base):
    """An app-scoped role `<app>:<name>`."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("app_id", "key", name="uq_roles_app_id_key"),
        UniqueConstraint("id", "app_id", name="uq_roles_id_app_id"),
        CheckConstraint(f"key ~ '{ROLE_KEY_RE}'", name="key_format"),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class RolePermission(Base):
    """A role holds a permission; `app_id` lets the database refuse cross-app links."""

    __tablename__ = "role_permissions"
    __table_args__ = (
        PrimaryKeyConstraint("role_id", "permission_id", name="pk_role_permissions"),
        ForeignKeyConstraint(
            ["role_id", "app_id"],
            ["roles.id", "roles.app_id"],
            ondelete="CASCADE",
            name="fk_role_permissions_role_same_app",
        ),
        ForeignKeyConstraint(
            ["permission_id", "app_id"],
            ["permissions.id", "permissions.app_id"],
            ondelete="CASCADE",
            name="fk_role_permissions_permission_same_app",
        ),
        Index("ix_role_permissions_permission_id", "permission_id"),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    permission_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    app_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class RoleAssignment(UUIDPkMixin, Base):
    """A user holds a role at app scope (`org_id` NULL) or in one organization."""

    __tablename__ = "role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role_id",
            "org_id",
            name="uq_role_assignments_user_role_org",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_role_assignments_role_id", "role_id"),
        Index("ix_role_assignments_org_id", "org_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), nullable=False)
    # FK to organizations is added in USR-06 (ticket scope).
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
