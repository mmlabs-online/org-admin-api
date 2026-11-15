"""audit log purge floor

Revision ID: d7e4b2a91c3f
Revises: af3eb84d2d5e
Create Date: 2026-11-15 15:00:00.000000

Retention purge (ADM-004c, ADR-0009 amendment): `audit_log_immutable()` still refuses every
UPDATE and TRUNCATE, and refuses a DELETE unless the transaction set
`SET LOCAL audit.allow_purge = 'on'` AND the row is older than 90 days. The 90-day floor is
hard-coded here on purpose: never read from env or settings (`MIN_DAYS` in
`app/modules/audit/purge.py` must match it; a test checks). The triggers keep their binding.
Written by hand: `alembic check` cannot see functions.

`downgrade()` restores the original function text byte for byte. It is safe: it only makes the
table stricter.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e4b2a91c3f"
down_revision: str | Sequence[str] | None = "af3eb84d2d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PURGE_FUNCTION = (
    "CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "BEGIN "
    "IF TG_OP = 'DELETE' THEN "
    "IF current_setting('audit.allow_purge', true) = 'on' "
    "AND OLD.occurred_at < now() - interval '90 days' THEN RETURN OLD; END IF; "
    "END IF; "
    "RAISE EXCEPTION 'audit_log is append-only'; "
    "END $$"
)

# Copied from c3f1a9d27b60 (`CREATE` → `CREATE OR REPLACE`; the body string is identical).
ORIGINAL_FUNCTION = (
    "CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(PURGE_FUNCTION)


def downgrade() -> None:
    """Downgrade schema. Restores the original append-only function (stricter)."""
    op.execute(ORIGINAL_FUNCTION)
