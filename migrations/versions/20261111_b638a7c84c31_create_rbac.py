"""create rbac

Revision ID: b638a7c84c31
Revises: 7b847bdc5230
Create Date: 2026-11-11 07:54:36.662841

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b638a7c84c31"
down_revision: str | Sequence[str] | None = "7b847bdc5230"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed ids of the seed rows (app `admin`, permission `admin:*`, role `admin:super-admin`).
ADMIN_APP_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
ADMIN_WILDCARD_PERMISSION_ID = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
ADMIN_SUPER_ADMIN_ROLE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000c1")

# Must match app/modules/rbac/models.py exactly (Alembic does not compare CHECKs).
APP_KEY_RE = r"^[a-z][a-z0-9-]{1,31}$"
PERMISSION_KEY_RE = r"^[a-z][a-z0-9-]{1,31}:([a-z][a-z0-9-]{0,47}:[a-z][a-z0-9-]{0,47}|\*)$"
ROLE_KEY_RE = r"^[a-z][a-z0-9-]{1,31}:[a-z][a-z0-9-]{0,47}$"


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    apps = op.create_table(
        "apps",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("join_policy", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("org_mode", sa.String(length=20), server_default="none", nullable=False),
        sa.Column(
            "origins",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("default_role_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "join_policy IN ('open', 'approval', 'closed')",
            name=op.f("ck_apps_join_policy_valid"),
        ),
        sa.CheckConstraint(f"key ~ '{APP_KEY_RE}'", name=op.f("ck_apps_key_format")),
        sa.CheckConstraint("org_mode IN ('none', 'required')", name=op.f("ck_apps_org_mode_valid")),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_apps_status_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_apps")),
        sa.UniqueConstraint("key", name="uq_apps_key"),
    )
    permissions = op.create_table(
        "permissions",
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "is_admin OR key NOT LIKE '%:*'", name=op.f("ck_permissions_wildcard_is_admin")
        ),
        sa.CheckConstraint(f"key ~ '{PERMISSION_KEY_RE}'", name=op.f("ck_permissions_key_format")),
        sa.ForeignKeyConstraint(
            ["app_id"], ["apps.id"], name=op.f("fk_permissions_app_id_apps"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permissions")),
        sa.UniqueConstraint("app_id", "key", name="uq_permissions_app_id_key"),
        sa.UniqueConstraint("id", "app_id", name="uq_permissions_id_app_id"),
    )
    roles = op.create_table(
        "roles",
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(f"key ~ '{ROLE_KEY_RE}'", name=op.f("ck_roles_key_format")),
        sa.ForeignKeyConstraint(
            ["app_id"], ["apps.id"], name=op.f("fk_roles_app_id_apps"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("app_id", "key", name="uq_roles_app_id_key"),
        sa.UniqueConstraint("id", "app_id", name="uq_roles_id_app_id"),
    )
    role_permissions = op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_id", "app_id"],
            ["permissions.id", "permissions.app_id"],
            name="fk_role_permissions_permission_same_app",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id", "app_id"],
            ["roles.id", "roles.app_id"],
            name="fk_role_permissions_role_same_app",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name="pk_role_permissions"),
    )
    op.create_index(
        "ix_role_permissions_permission_id", "role_permissions", ["permission_id"], unique=False
    )
    op.create_table(
        "role_assignments",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        # FK to organizations is added in USR-06 (ticket scope).
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_role_assignments_granted_by_users"),
            ondelete="SET NULL",
        ),
        # No ON DELETE: a role that has assignments cannot be deleted.
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name=op.f("fk_role_assignments_role_id_roles")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_role_assignments_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_assignments")),
        sa.UniqueConstraint(
            "user_id",
            "role_id",
            "org_id",
            name="uq_role_assignments_user_role_org",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_role_assignments_org_id", "role_assignments", ["org_id"], unique=False)
    op.create_index("ix_role_assignments_role_id", "role_assignments", ["role_id"], unique=False)
    # Created after `roles` (circular reference). NO ACTION (not RESTRICT): checked at the end of
    # the statement, so deleting an app still cascades through its default role.
    op.create_foreign_key(
        "fk_apps_default_role_same_app",
        "apps",
        "roles",
        ["default_role_id", "id"],
        ["id", "app_id"],
    )

    # Seed: the admin API is app `admin` (closed, no member role) with a super-admin role.
    op.bulk_insert(
        apps,
        [
            {
                "id": ADMIN_APP_ID,
                "key": "admin",
                "name": "mmlabs Admin",
                "status": "active",
                "join_policy": "closed",
                "org_mode": "none",
                "origins": [],
            }
        ],
    )
    op.bulk_insert(
        permissions,
        [
            {
                "id": ADMIN_WILDCARD_PERMISSION_ID,
                "app_id": ADMIN_APP_ID,
                "key": "admin:*",
                "description": "All admin permissions",
                "is_admin": True,
            }
        ],
    )
    op.bulk_insert(
        roles,
        [
            {
                "id": ADMIN_SUPER_ADMIN_ROLE_ID,
                "app_id": ADMIN_APP_ID,
                "key": "admin:super-admin",
                "name": "Super admin",
                "is_system": True,
            }
        ],
    )
    op.bulk_insert(
        role_permissions,
        [
            {
                "role_id": ADMIN_SUPER_ADMIN_ROLE_ID,
                "permission_id": ADMIN_WILDCARD_PERMISSION_ID,
                "app_id": ADMIN_APP_ID,
            }
        ],
    )


def downgrade() -> None:
    """Downgrade schema (the seed rows go with the tables)."""
    op.drop_constraint("fk_apps_default_role_same_app", "apps", type_="foreignkey")
    op.drop_index("ix_role_assignments_role_id", table_name="role_assignments")
    op.drop_index("ix_role_assignments_org_id", table_name="role_assignments")
    op.drop_table("role_assignments")
    op.drop_index("ix_role_permissions_permission_id", table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_table("permissions")
    op.drop_table("apps")
