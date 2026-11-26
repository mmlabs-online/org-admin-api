"""adm042 usage counters events

Revision ID: c91e4b7a3d58
Revises: b7d3f1a9c542
Create Date: 2026-11-22 12:00:00.000000

ADM-042 (ADR-0022 §5, §10): `usage_counters` (per-period totals) and `usage_events` (ledger and
idempotency record, unique per (app, idempotency_key)), and the runtime scopes `usage:read`,
`usage:write` and `limits:write` in `ck_app_credentials_scope_valid`.

`downgrade()` drops both tables (events first), deletes the three runtime-scope keys (revocation
by removal; the old CHECK would reject them), then restores the USR-29 list.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c91e4b7a3d58"
down_revision: str | Sequence[str] | None = "b7d3f1a9c542"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE_CK = "ck_app_credentials_scope_valid"
_PERIOD = "period IN ('day', 'week', 'month')"
_METRIC_FK = (["metric_id", "app_id"], ["usage_metrics.id", "usage_metrics.app_id"])


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "usage_counters",
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.String(length=10), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
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
        sa.CheckConstraint(_PERIOD, name=op.f("ck_usage_counters_period_valid")),
        sa.CheckConstraint(
            "used BETWEEN 0 AND 9007199254740991", name=op.f("ck_usage_counters_used_range")
        ),
        sa.ForeignKeyConstraint(
            *_METRIC_FK,
            name=op.f("fk_usage_counters_metric_id_app_id_usage_metrics"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_usage_counters_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_counters")),
        sa.UniqueConstraint(
            "user_id",
            "metric_id",
            "period",
            "period_start",
            name="uq_usage_counters_user_id_metric_id_period_period_start",
        ),
    )
    op.create_table(
        "usage_events",
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("period", sa.String(length=10), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("settled_amount", sa.BigInteger(), nullable=True),
        sa.Column(
            "state", sa.String(length=10), server_default=sa.text("'consumed'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "idempotency_key ~ '^[!-~]{1,128}$'",
            name=op.f("ck_usage_events_idempotency_key_format"),
        ),
        sa.CheckConstraint(_PERIOD, name=op.f("ck_usage_events_period_valid")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_usage_events_amount_positive")),
        sa.CheckConstraint(
            "settled_amount IS NULL OR settled_amount > 0",
            name=op.f("ck_usage_events_settled_amount_positive"),
        ),
        sa.CheckConstraint(
            "state IN ('consumed', 'settled', 'refunded')", name=op.f("ck_usage_events_state_valid")
        ),
        sa.CheckConstraint(
            "(state = 'consumed' AND settled_at IS NULL AND settled_amount IS NULL "
            "AND refunded_at IS NULL) "
            "OR (state = 'settled' AND settled_at IS NOT NULL AND settled_amount IS NOT NULL "
            "AND refunded_at IS NULL) "
            "OR (state = 'refunded' AND refunded_at IS NOT NULL "
            "AND (settled_at IS NULL) = (settled_amount IS NULL))",
            name=op.f("ck_usage_events_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            *_METRIC_FK,
            name=op.f("fk_usage_events_metric_id_app_id_usage_metrics"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_usage_events_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
        sa.UniqueConstraint(
            "app_id", "idempotency_key", name="uq_usage_events_app_id_idempotency_key"
        ),
    )
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])
    op.create_index(
        "ix_usage_events_user_id_metric_id_created_at",
        "usage_events",
        ["user_id", "metric_id", "created_at"],
    )
    op.drop_constraint(op.f(_SCOPE_CK), "app_credentials", type_="check")
    op.create_check_constraint(
        op.f(_SCOPE_CK),
        "app_credentials",
        "scope IN ('manifest', 'releases', 'org-members:read', 'usage:read', 'usage:write', "
        "'limits:write')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("usage_events")
    op.drop_table("usage_counters")
    op.execute(
        "DELETE FROM app_credentials WHERE scope IN ('usage:read', 'usage:write', 'limits:write')"
    )
    op.drop_constraint(op.f(_SCOPE_CK), "app_credentials", type_="check")
    op.create_check_constraint(
        op.f(_SCOPE_CK), "app_credentials", "scope IN ('manifest', 'releases', 'org-members:read')"
    )
