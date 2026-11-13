"""add email_tokens created_at index

Revision ID: 4d4b5d67dc44
Revises: 91a78d0496f9
Create Date: 2026-11-13 03:53:18.304003

Index on `email_tokens(created_at)` for the global daily-cap count and the retention cleanup
(USR-04d). Expand-only; plain `CREATE INDEX` because the table is small (bounded by the cap).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d4b5d67dc44"
down_revision: str | Sequence[str] | None = "91a78d0496f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_email_tokens_created_at", "email_tokens", ["created_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_email_tokens_created_at", table_name="email_tokens")
