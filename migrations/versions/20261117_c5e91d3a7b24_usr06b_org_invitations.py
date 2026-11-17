"""usr06b org invitations

Revision ID: c5e91d3a7b24
Revises: b4d82f6a1c93
Create Date: 2026-11-17 14:00:00.000000

USR-06b: expand-only except the `email_send_log` CHECK swap.

1. New table `org_invitations` (only SHA-256(token) and the email HMAC are stored).
2. `email_send_log.purpose` CHECK widened to `('verify', 'reset', 'invite')`, added `NOT VALID`
   and then validated. `DROP CONSTRAINT` takes ACCESS EXCLUSIVE until commit and `VALIDATE`
   runs in the same transaction, so the split saves no lock time; `email_send_log` is small.

`downgrade()` is lossy for invitations and `invite` send-log rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e91d3a7b24"
down_revision: str | Sequence[str] | None = "b4d82f6a1c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PURPOSE_CK = "ck_email_send_log_purpose_valid"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "org_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("email_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(email_hmac) = 32", name=op.f("ck_org_invitations_email_hmac_length")
        ),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name=op.f("ck_org_invitations_token_hash_length")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_org_invitations_expiry_after_creation")
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name=op.f("ck_org_invitations_not_accepted_and_revoked"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_org_invitations_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id", "app_id"],
            ["roles.id", "roles.app_id"],
            name="fk_org_invitations_role_same_app",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name="fk_org_invitations_invited_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["users.id"],
            name="fk_org_invitations_accepted_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_org_invitations_token_hash")),
    )
    op.create_index(
        "uq_org_invitations_pending",
        "org_invitations",
        ["org_id", "app_id", "email_hmac"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_index(
        "ix_org_invitations_org_id_created_at", "org_invitations", ["org_id", "created_at"]
    )
    op.create_index(
        "ix_org_invitations_invited_by_created_at",
        "org_invitations",
        ["invited_by", "created_at"],
    )
    op.create_index("ix_org_invitations_role_id_app_id", "org_invitations", ["role_id", "app_id"])

    op.drop_constraint(op.f(PURPOSE_CK), "email_send_log", type_="check")
    op.execute(
        f"ALTER TABLE email_send_log ADD CONSTRAINT {PURPOSE_CK} "
        "CHECK (purpose IN ('verify', 'reset', 'invite')) NOT VALID"
    )
    op.execute(f"ALTER TABLE email_send_log VALIDATE CONSTRAINT {PURPOSE_CK}")


def downgrade() -> None:
    """Downgrade schema (drops every invitation and every `invite` send-log row)."""
    op.drop_index("ix_org_invitations_role_id_app_id", table_name="org_invitations")
    op.drop_index("ix_org_invitations_invited_by_created_at", table_name="org_invitations")
    op.drop_index("ix_org_invitations_org_id_created_at", table_name="org_invitations")
    op.drop_index(
        "uq_org_invitations_pending",
        table_name="org_invitations",
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    op.drop_table("org_invitations")

    op.execute("DELETE FROM email_send_log WHERE purpose = 'invite'")
    op.drop_constraint(op.f(PURPOSE_CK), "email_send_log", type_="check")
    op.create_check_constraint(op.f(PURPOSE_CK), "email_send_log", "purpose IN ('verify', 'reset')")
