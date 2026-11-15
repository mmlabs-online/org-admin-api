"""add audit log outcome

Revision ID: af3eb84d2d5e
Revises: c3f1a9d27b60
Create Date: 2026-11-15 09:29:02.174288

`audit_log.outcome` (`success|failure|denied`, ADM-004b). Additive: `ADD COLUMN` with a constant
default is metadata-only on PG17 (no rewrite, no backfill, no UPDATE, so the append-only trigger
never fires); existing rows read `success`. The CHECK is written by hand (`alembic check` cannot
see CHECKs). A partial index serves the selective `outcome=failure|denied` filter.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "af3eb84d2d5e"
down_revision: str | Sequence[str] | None = "c3f1a9d27b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "audit_log",
        sa.Column("outcome", sa.Text(), server_default=sa.text("'success'"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_audit_log_outcome_valid"),
        "audit_log",
        "outcome IN ('success', 'failure', 'denied')",
    )
    op.create_index(
        "ix_audit_log_outcome",
        "audit_log",
        ["outcome", "occurred_at", "id"],
        unique=False,
        postgresql_where=sa.text("outcome IN ('failure', 'denied')"),
    )


def downgrade() -> None:
    """Downgrade schema. Drops the column (and its CHECK); no other data is touched."""
    op.drop_index("ix_audit_log_outcome", table_name="audit_log")
    op.drop_column("audit_log", "outcome")
