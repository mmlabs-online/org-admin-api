"""create sessions

Revision ID: 83936724f780
Revises: b638a7c84c31
Create Date: 2026-11-12 03:41:15.567511

Device and account sessions (USR-03, ADR-0004) and per-email login failure counters (D11,
ADR-0002 §5). New tables only; existing tables are untouched. No `refresh_tokens` table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "83936724f780"
down_revision: str | Sequence[str] | None = "b638a7c84c31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now_column(name: str) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    """Upgrade schema."""
    # CHECK texts must match app/modules/sessions/models.py and app/modules/auth/models.py.
    op.create_table(
        "device_sessions",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("prev_token_hash", sa.LargeBinary(), nullable=True),
        sa.Column("prev_valid_until", sa.DateTime(timezone=True), nullable=True),
        _now_column("created_at"),
        _now_column("last_seen_at"),
        _now_column("rotated_at"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(prev_token_hash IS NULL) = (prev_valid_until IS NULL)",
            name=op.f("ck_device_sessions_prev_pair"),
        ),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name=op.f("ck_device_sessions_token_hash_length")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_device_sessions_token_hash")),
    )
    # Not unique: a previous hash is only looked up, never enforced.
    op.create_index(
        "ix_device_sessions_prev_token_hash",
        "device_sessions",
        ["prev_token_hash"],
        unique=False,
        postgresql_where=sa.text("prev_token_hash IS NOT NULL"),
    )

    op.create_table(
        "account_sessions",
        sa.Column("device_session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        _now_column("created_at"),
        _now_column("last_used_at"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_session_id"],
            ["device_sessions.id"],
            name=op.f("fk_account_sessions_device_session_id_device_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_account_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_sessions")),
    )
    # Leading column `device_session_id` also serves the FK; no separate index needed.
    op.create_index(
        "uq_account_sessions_active",
        "account_sessions",
        ["device_session_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("ix_account_sessions_user_id", "account_sessions", ["user_id"], unique=False)

    # No FK to users: unknown emails are counted too (D11 identical responses).
    op.create_table(
        "login_failures",
        sa.Column("email_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        _now_column("last_failed_at"),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(email_hmac) = 32", name=op.f("ck_login_failures_email_hmac_length")
        ),
        sa.CheckConstraint("failures >= 1", name=op.f("ck_login_failures_failures_positive")),
        sa.PrimaryKeyConstraint("email_hmac", name=op.f("pk_login_failures")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("login_failures")
    op.drop_index("ix_account_sessions_user_id", table_name="account_sessions")
    op.drop_index("uq_account_sessions_active", table_name="account_sessions")
    op.drop_table("account_sessions")
    op.drop_index("ix_device_sessions_prev_token_hash", table_name="device_sessions")
    op.drop_table("device_sessions")
