"""adm039 usage metrics

Revision ID: 8c2f6a1d4e93
Revises: 5b7d1e9c3a42
Create Date: 2026-11-20 14:00:00.000000

ADM-039: table `usage_metrics` (per-app metric, immutable key and unit, default limit, period,
status; RESTRICT on `apps`) and the admin permissions `admin:usage:read` / `admin:usage:write`
(`is_admin`, granted to no role, `ON CONFLICT DO NOTHING`).

`downgrade()` deletes both permission keys (their `role_permissions` rows cascade, which is lossy
for roles a super-admin added them to) and drops the table with its metrics. Later usage tables
(ADM-040/042) reference this one and must be downgraded first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c2f6a1d4e93"
down_revision: str | Sequence[str] | None = "5b7d1e9c3a42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_APP_ID = "00000000-0000-4000-8000-0000000000a1"
PERMISSIONS = [
    ("admin:usage:read", "Read usage metrics, defaults and periods of every app"),
    (
        "admin:usage:write",
        "Create usage metrics and change their name, default limit, period and status",
    ),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "usage_metrics",
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=48), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("default_limit", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.String(length=10), server_default=sa.text("'week'"), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
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
            "key ~ '^[a-z][a-z0-9-]{1,47}$'", name=op.f("ck_usage_metrics_key_format")
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 100",
            name=op.f("ck_usage_metrics_display_name_length"),
        ),
        sa.CheckConstraint(
            "unit ~ '^[a-z][a-z0-9-]{0,31}$'", name=op.f("ck_usage_metrics_unit_format")
        ),
        sa.CheckConstraint(
            "default_limit BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_usage_metrics_default_limit_range"),
        ),
        sa.CheckConstraint(
            "period IN ('day', 'week', 'month')", name=op.f("ck_usage_metrics_period_valid")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name=op.f("ck_usage_metrics_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["app_id"], ["apps.id"], name="fk_usage_metrics_app_id_apps", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_metrics")),
        sa.UniqueConstraint("app_id", "key", name="uq_usage_metrics_app_id_key"),
        sa.UniqueConstraint("id", "app_id", name="uq_usage_metrics_id_app_id"),
    )
    insert = sa.text(
        "INSERT INTO permissions (id, app_id, key, description, is_admin) "
        "VALUES (gen_random_uuid(), CAST(:app_id AS uuid), :key, :description, true) "
        "ON CONFLICT (app_id, key) DO NOTHING"
    )
    for key, description in PERMISSIONS:
        op.execute(insert.bindparams(app_id=ADMIN_APP_ID, key=key, description=description))


def downgrade() -> None:
    """Downgrade schema (deletes the permissions and all metrics)."""
    delete = sa.text(
        "DELETE FROM permissions WHERE app_id = CAST(:app_id AS uuid) AND key IN :keys"
    ).bindparams(sa.bindparam("keys", expanding=True))
    op.execute(delete.bindparams(app_id=ADMIN_APP_ID, keys=[k for k, _ in PERMISSIONS]))
    op.drop_table("usage_metrics")
