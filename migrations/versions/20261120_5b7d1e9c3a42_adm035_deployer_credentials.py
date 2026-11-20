"""adm035 deployer credentials

Revision ID: 5b7d1e9c3a42
Revises: 89fd8d667a25
Create Date: 2026-11-20 09:00:00.000000

ADM-035: expand-only. New table `deployer_credentials` (ADR-0021 §2). `downgrade()` drops the
table and every issued deployer key.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b7d1e9c3a42"
down_revision: str | Sequence[str] | None = "89fd8d667a25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "deployer_credentials",
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
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "key_id ~ '^[0-9a-f]{16}$'", name=op.f("ck_deployer_credentials_key_id_format")
        ),
        sa.CheckConstraint(
            "octet_length(secret_hash) = 32",
            name=op.f("ck_deployer_credentials_secret_hash_length"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_deployer_credentials_created_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployer_credentials")),
        sa.UniqueConstraint("key_id", name=op.f("uq_deployer_credentials_key_id")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("deployer_credentials")
