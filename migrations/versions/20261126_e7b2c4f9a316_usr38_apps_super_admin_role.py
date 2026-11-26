"""usr38 apps super admin role

Revision ID: e7b2c4f9a316
Revises: d4a8e2c6f175
Create Date: 2026-11-26 10:00:00.000000

USR-38 (ADR-0030): `apps.super_admin_role_id`, the role an `org_mode = required` app designates
as its super-admin role. Same-app composite FK (like `org_owner_role_id`, no ACTION so app
delete still cascades and a role delete is refused), and three CHECKs: only for
`org_mode = 'required'`, never the `admin` app, distinct from the default and org-owner roles.
No data step: every row stays NULL.

Lossy downgrade: the designation is lost; assignments of the formerly designated role remain
as ordinary app-scope assignments (no preflight).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7b2c4f9a316"
down_revision: str | Sequence[str] | None = "d4a8e2c6f175"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("apps", sa.Column("super_admin_role_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_apps_super_admin_role_same_app",
        "apps",
        "roles",
        ["super_admin_role_id", "id"],
        ["id", "app_id"],
    )
    op.create_check_constraint(
        op.f("ck_apps_super_admin_requires_org_mode"),
        "apps",
        "super_admin_role_id IS NULL OR org_mode = 'required'",
    )
    op.create_check_constraint(
        op.f("ck_apps_super_admin_not_admin_app"),
        "apps",
        "super_admin_role_id IS NULL OR key <> 'admin'",
    )
    op.create_check_constraint(
        op.f("ck_apps_super_admin_role_distinct"),
        "apps",
        "super_admin_role_id IS NULL OR ((default_role_id IS NULL "
        "OR super_admin_role_id <> default_role_id) "
        "AND (org_owner_role_id IS NULL OR super_admin_role_id <> org_owner_role_id))",
    )


def downgrade() -> None:
    """Downgrade schema (drops the designation values)."""
    op.drop_constraint(op.f("ck_apps_super_admin_role_distinct"), "apps", type_="check")
    op.drop_constraint(op.f("ck_apps_super_admin_not_admin_app"), "apps", type_="check")
    op.drop_constraint(op.f("ck_apps_super_admin_requires_org_mode"), "apps", type_="check")
    op.drop_constraint("fk_apps_super_admin_role_same_app", "apps", type_="foreignkey")
    op.drop_column("apps", "super_admin_role_id")
