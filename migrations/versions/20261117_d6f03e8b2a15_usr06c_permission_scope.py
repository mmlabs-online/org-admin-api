"""usr06c permission scope

Revision ID: d6f03e8b2a15
Revises: c5e91d3a7b24
Create Date: 2026-11-17 18:00:00.000000

USR-06c (ADR-0015): `permissions.scope` (`app` | `org`) and `apps.default_role_id <>
org_owner_role_id`.

1. Preflight: abort (no auto-fix) if an app already has default role = org owner role.
2. Add `permissions.scope` NOT NULL DEFAULT 'app' (metadata-only on PG 17).
3. Backfill, `org_mode = 'required'` apps only: `<app>:org:create` stays `app`;
   `<app>:org:invite` is `org`; a permission held by the default role and not by the owner
   role is `app`; everything else is `org`. `role_permissions` is never written.
4. CHECKs `ck_permissions_scope_valid` and `ck_apps_default_role_not_org_owner`.

`downgrade()` drops the CHECKs and the column. It is lossy for `scope`: a re-upgrade recomputes
it from the current role contents.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6f03e8b2a15"
down_revision: str | Sequence[str] | None = "c5e91d3a7b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCOPE_CK = "ck_permissions_scope_valid"
OWNER_CK = "ck_apps_default_role_not_org_owner"


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    bad = conn.execute(
        sa.text("SELECT count(*) FROM apps WHERE default_role_id = org_owner_role_id")
    ).scalar_one()
    if bad:
        raise RuntimeError(
            f"USR-06c: {bad} apps have default_role_id = org_owner_role_id; "
            "fix them before upgrading"
        )
    op.add_column(
        "permissions",
        sa.Column("scope", sa.String(length=10), server_default="app", nullable=False),
    )
    op.execute(
        """
        UPDATE permissions p SET scope = 'org'
        FROM apps a
        WHERE p.app_id = a.id AND a.org_mode = 'required'
          AND p.key <> a.key || ':org:create'
          AND (
            p.key = a.key || ':org:invite'
            OR NOT (
              a.default_role_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM role_permissions rp
                          WHERE rp.role_id = a.default_role_id AND rp.permission_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM role_permissions rp
                              WHERE rp.role_id = a.org_owner_role_id AND rp.permission_id = p.id)
            )
          )
        """
    )
    op.create_check_constraint(op.f(SCOPE_CK), "permissions", "scope IN ('app', 'org')")
    op.create_check_constraint(
        op.f(OWNER_CK),
        "apps",
        "default_role_id IS NULL OR org_owner_role_id IS NULL "
        "OR default_role_id <> org_owner_role_id",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f(OWNER_CK), "apps", type_="check")
    op.drop_constraint(op.f(SCOPE_CK), "permissions", type_="check")
    op.drop_column("permissions", "scope")
