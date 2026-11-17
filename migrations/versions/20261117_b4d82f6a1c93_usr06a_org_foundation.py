"""usr06a org foundation

Revision ID: b4d82f6a1c93
Revises: a7c3e5f19b42
Create Date: 2026-11-17 09:00:00.000000

USR-06a, in three ordered steps:

1. `organizations.created_by` (nullable, `ON DELETE SET NULL`, no backfill: existing rows stay
   NULL) and a partial index for the FK scans and the per-user org cap.
2. FK `role_assignments.org_id` -> `organizations.id` `ON DELETE RESTRICT`. A preflight aborts
   loudly when an `org_id` points at no organization: such rows are never deleted or set to
   NULL (NULL would widen an org-scoped role to app scope).
3. `apps.org_owner_role_id` (same-app composite FK like `default_role_id`, no ACTION so app
   delete still cascades) and a CHECK that it is set only for `org_mode = 'required'`.

`downgrade()` drops all three; it is lossy only for the `created_by` and `org_owner_role_id`
values. Production never downgrades past this revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d82f6a1c93"
down_revision: str | Sequence[str] | None = "a7c3e5f19b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Step 1: organizations.created_by
    op.add_column("organizations", sa.Column("created_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_organizations_created_by_users",
        "organizations",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_organizations_created_by",
        "organizations",
        ["created_by"],
        unique=False,
        postgresql_where=sa.text("created_by IS NOT NULL"),
    )

    # Step 2: role_assignments.org_id FK (preflight first)
    orphans = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM role_assignments ra WHERE ra.org_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM organizations o WHERE o.id = ra.org_id)"
        )
    )
    if orphans:
        raise RuntimeError(
            f"USR-06a: {orphans} role_assignments rows reference missing organizations; "
            "resolve them before upgrading"
        )
    op.create_foreign_key(
        "fk_role_assignments_org_id_organizations",
        "role_assignments",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Step 3: apps.org_owner_role_id
    op.add_column("apps", sa.Column("org_owner_role_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_apps_org_owner_role_same_app",
        "apps",
        "roles",
        ["org_owner_role_id", "id"],
        ["id", "app_id"],
    )
    op.create_check_constraint(
        op.f("ck_apps_org_owner_requires_org_mode"),
        "apps",
        "org_owner_role_id IS NULL OR org_mode = 'required'",
    )


def downgrade() -> None:
    """Downgrade schema (reverse order; drops `created_by` and `org_owner_role_id` values)."""
    op.drop_constraint(op.f("ck_apps_org_owner_requires_org_mode"), "apps", type_="check")
    op.drop_constraint("fk_apps_org_owner_role_same_app", "apps", type_="foreignkey")
    op.drop_column("apps", "org_owner_role_id")

    op.drop_constraint(
        "fk_role_assignments_org_id_organizations", "role_assignments", type_="foreignkey"
    )

    op.drop_index(
        "ix_organizations_created_by",
        table_name="organizations",
        postgresql_where=sa.text("created_by IS NOT NULL"),
    )
    op.drop_constraint("fk_organizations_created_by_users", "organizations", type_="foreignkey")
    op.drop_column("organizations", "created_by")
