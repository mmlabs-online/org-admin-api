"""usr03g join requests

Revision ID: a9d4c27e6b13
Revises: f3a8d51c9e27
Create Date: 2026-11-17 18:00:00.000000

USR-03g: expand-only. New table `join_requests` for `approval` apps. A new table, so no lock
concerns. `downgrade()` is lossy: every join request is dropped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9d4c27e6b13"
down_revision: str | Sequence[str] | None = "f3a8d51c9e27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "join_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_join_requests_status_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)",
            name=op.f("ck_join_requests_decided_iff_not_pending"),
        ),
        sa.ForeignKeyConstraint(
            ["app_id"], ["apps.id"], name="fk_join_requests_app_id_apps", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_join_requests_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name="fk_join_requests_decided_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_join_requests")),
    )
    op.create_index(
        "uq_join_requests_pending",
        "join_requests",
        ["app_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_join_requests_app_id_status_created_at_id",
        "join_requests",
        ["app_id", "status", "created_at", "id"],
    )
    op.create_index(
        "ix_join_requests_user_id_app_id_created_at",
        "join_requests",
        ["user_id", "app_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema (drops every join request)."""
    op.drop_index("ix_join_requests_user_id_app_id_created_at", table_name="join_requests")
    op.drop_index("ix_join_requests_app_id_status_created_at_id", table_name="join_requests")
    op.drop_index(
        "uq_join_requests_pending",
        table_name="join_requests",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("join_requests")
