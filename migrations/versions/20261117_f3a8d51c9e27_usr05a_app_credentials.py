"""usr05a app credentials

Revision ID: f3a8d51c9e27
Revises: d6f03e8b2a15
Create Date: 2026-11-17 18:00:00.000000

USR-05a: expand-only. New table `app_credentials` (ADR-0016): per-app API keys, only
SHA-256(secret) is stored. `downgrade()` drops the table and every issued credential.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a8d51c9e27"
down_revision: str | Sequence[str] | None = "d6f03e8b2a15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "app_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("secret_hash", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "key_id ~ '^[0-9a-f]{16}$'", name=op.f("ck_app_credentials_key_id_format")
        ),
        sa.CheckConstraint(
            "octet_length(secret_hash) = 32", name=op.f("ck_app_credentials_secret_hash_length")
        ),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["apps.id"],
            name="fk_app_credentials_app_id_apps",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_app_credentials_created_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_credentials")),
        sa.UniqueConstraint("key_id", name=op.f("uq_app_credentials_key_id")),
    )
    op.create_index(
        "ix_app_credentials_app_id_created_at", "app_credentials", ["app_id", "created_at"]
    )
    op.create_index(
        "ix_app_credentials_app_id_active",
        "app_credentials",
        ["app_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema (drops every issued credential)."""
    op.drop_index("ix_app_credentials_app_id_active", table_name="app_credentials")
    op.drop_index("ix_app_credentials_app_id_created_at", table_name="app_credentials")
    op.drop_table("app_credentials")
