"""adm021 audit log org_id index

Revision ID: d9e4b2a61f73
Revises: c7a3e91f5d28
Create Date: 2026-11-18 12:00:00.000000

ADM-021: partial index serving the `org_id` filter of `GET /api/v1/audit-logs`. Plain
`CREATE INDEX`, like `ix_audit_log_outcome`: the table is young and small in production (ADM-004
shipped 2026-11-15) and the build lock is brief. For a large table follow the `docs/deploy.md`
note "Future audit DDL on a large table" and use `CONCURRENTLY`. DDL is not blocked by the
append-only trigger; `downgrade()` is lossless.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e4b2a61f73"
down_revision: str | Sequence[str] | None = "c7a3e91f5d28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_audit_log_org_id",
        "audit_log",
        ["org_id", "occurred_at", "id"],
        unique=False,
        postgresql_where=sa.text("org_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_audit_log_org_id", table_name="audit_log")
