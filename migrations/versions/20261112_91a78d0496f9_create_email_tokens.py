"""create email tokens

Revision ID: 91a78d0496f9
Revises: 83936724f780
Create Date: 2026-11-12 15:16:34.550874

One-time email tokens and the per-email send log for resend limits (USR-04, ADR-0007). New
tables only; existing tables are untouched (expand-only).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "91a78d0496f9"
down_revision: str | Sequence[str] | None = "83936724f780"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now_column(name: str) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    """Upgrade schema."""
    # CHECK texts must match app/modules/auth/models.py.
    op.create_table(
        "email_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=10), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        _now_column("created_at"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('verify', 'reset')", name=op.f("ck_email_tokens_purpose_valid")
        ),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name=op.f("ck_email_tokens_token_hash_length")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_email_tokens_expiry_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_email_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_email_tokens_token_hash")),
    )
    # Leading `user_id` also serves the FK (cascade on user delete).
    op.create_index(
        "ix_email_tokens_user_id_purpose_active",
        "email_tokens",
        ["user_id", "purpose"],
        unique=False,
        postgresql_where=sa.text("used_at IS NULL"),
    )

    # No FK to users and no email: keyed by HMAC so unknown emails can be counted (USR-04b).
    op.create_table(
        "email_send_log",
        sa.Column("email_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("purpose", sa.String(length=10), nullable=False),
        _now_column("sent_at"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "octet_length(email_hmac) = 32", name=op.f("ck_email_send_log_email_hmac_length")
        ),
        sa.CheckConstraint(
            "purpose IN ('verify', 'reset')", name=op.f("ck_email_send_log_purpose_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_send_log")),
    )
    op.create_index(
        "ix_email_send_log_email_hmac_purpose_sent_at",
        "email_send_log",
        ["email_hmac", "purpose", "sent_at"],
        unique=False,
    )
    op.create_index("ix_email_send_log_sent_at", "email_send_log", ["sent_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_email_send_log_sent_at", table_name="email_send_log")
    op.drop_index("ix_email_send_log_email_hmac_purpose_sent_at", table_name="email_send_log")
    op.drop_table("email_send_log")
    op.drop_index("ix_email_tokens_user_id_purpose_active", table_name="email_tokens")
    op.drop_table("email_tokens")
