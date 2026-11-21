"""adm040 usage limit overrides

Revision ID: 911eb99ddf8b
Revises: 58a494f0449e
Create Date: 2026-11-21 00:23:18.990873

ADM-040: table `usage_limit_overrides` (one row per user and metric: limit, optional `expires_at`,
`created_by`). CASCADE on the user, RESTRICT on the metric, SET NULL on `created_by`. No reason
column: the reason lives in the audit entry (ADR-0024 point 5).

`downgrade()` drops the table with every override (lossy); the audit log keeps the history.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "911eb99ddf8b"
down_revision: str | Sequence[str] | None = "58a494f0449e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "usage_limit_overrides",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.Uuid(), nullable=False),
        sa.Column("limit_value", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
            "limit_value BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_usage_limit_overrides_limit_value_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_usage_limit_overrides_created_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["metric_id"],
            ["usage_metrics.id"],
            name="fk_usage_limit_overrides_metric_id_usage_metrics",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_usage_limit_overrides_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_limit_overrides")),
        sa.UniqueConstraint(
            "user_id", "metric_id", name="uq_usage_limit_overrides_user_id_metric_id"
        ),
    )
    op.create_index(
        "ix_usage_limit_overrides_metric_id", "usage_limit_overrides", ["metric_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_usage_limit_overrides_metric_id", table_name="usage_limit_overrides")
    op.drop_table("usage_limit_overrides")
