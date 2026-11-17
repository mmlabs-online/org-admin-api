"""adm006 org entitlements

Revision ID: c8a1f4e72d95
Revises: a9d4c27e6b13
Create Date: 2026-11-17 20:00:00.000000

ADM-006: expand-only. New table `org_entitlements` (RESTRICT FKs: an entitlement is a grant, so
org/app deletes must refuse instead of dropping it unaudited) and the two admin permissions
`admin:entitlements:read|write` (`is_admin`, granted to no role), `ON CONFLICT DO NOTHING`.

`downgrade()` is lossy: every entitlement is dropped, and the two permissions are deleted (their
`role_permissions` rows cascade).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c8a1f4e72d95"
down_revision: str | Sequence[str] | None = "a9d4c27e6b13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_APP_ID = "00000000-0000-4000-8000-0000000000a1"
PERMISSIONS = [
    ("admin:entitlements:read", "Read organization entitlements"),
    ("admin:entitlements:write", "Grant, update and revoke organization entitlements"),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "org_entitlements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("plan", sa.String(length=63), nullable=False),
        sa.Column(
            "limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.CheckConstraint(
            "plan ~ '^[a-z0-9][a-z0-9_-]{0,62}$'", name=op.f("ck_org_entitlements_plan_format")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(limits) = 'object'", name=op.f("ck_org_entitlements_limits_object")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_org_entitlements_org_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["apps.id"],
            name="fk_org_entitlements_app_id_apps",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_entitlements")),
        sa.UniqueConstraint("org_id", "app_id", name="uq_org_entitlements_org_id_app_id"),
    )
    op.create_index("ix_org_entitlements_app_id", "org_entitlements", ["app_id"])
    insert = sa.text(
        "INSERT INTO permissions (id, app_id, key, description, is_admin) "
        "VALUES (gen_random_uuid(), CAST(:app_id AS uuid), :key, :description, true) "
        "ON CONFLICT (app_id, key) DO NOTHING"
    )
    for key, description in PERMISSIONS:
        op.execute(insert.bindparams(app_id=ADMIN_APP_ID, key=key, description=description))


def downgrade() -> None:
    """Downgrade schema (drops every entitlement and the two permissions)."""
    delete = sa.text(
        "DELETE FROM permissions WHERE app_id = CAST(:app_id AS uuid) AND key IN :keys"
    ).bindparams(sa.bindparam("keys", expanding=True))
    op.execute(delete.bindparams(app_id=ADMIN_APP_ID, keys=[k for k, _ in PERMISSIONS]))
    op.drop_index("ix_org_entitlements_app_id", table_name="org_entitlements")
    op.drop_table("org_entitlements")
