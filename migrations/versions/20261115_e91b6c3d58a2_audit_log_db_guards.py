"""audit log db guards

Revision ID: e91b6c3d58a2
Revises: d7e4b2a91c3f
Create Date: 2026-11-15 18:00:00.000000

DB guards (ADM-004d, ADR-0009 amendment): `audit_log_immutable()` also refuses to DELETE an
`audit_log.purged` row, even under the purge latch (the purge trail is kept forever), and a
`BEFORE INSERT` trigger stamps `occurred_at := now()` (no backdated rows). Written by hand:
`alembic check` cannot see functions or triggers.

`downgrade()` drops the stamp trigger and restores `d7e4b2a91c3f`'s function byte for byte. It
loosens the table: production never downgrades past this revision (docs/deploy.md).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e91b6c3d58a2"
down_revision: str | Sequence[str] | None = "d7e4b2a91c3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARD_FUNCTION = (
    "CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "BEGIN "
    "IF TG_OP = 'DELETE' THEN "
    "IF current_setting('audit.allow_purge', true) = 'on' "
    "AND OLD.occurred_at < now() - interval '90 days' "
    "AND OLD.action <> 'audit_log.purged' THEN RETURN OLD; END IF; "
    "END IF; "
    "RAISE EXCEPTION 'audit_log is append-only'; "
    "END $$"
)

# `now()`, not `clock_timestamp()`: rows of one transaction share `occurred_at` (ADR-0009).
STAMP_FUNCTION = (
    "CREATE FUNCTION audit_log_stamp_occurred_at() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "BEGIN NEW.occurred_at := now(); RETURN NEW; END $$"
)
STAMP_TRIGGER = (
    "CREATE TRIGGER audit_log_stamp_occurred_at BEFORE INSERT ON audit_log "
    "FOR EACH ROW EXECUTE FUNCTION audit_log_stamp_occurred_at()"
)

# Copied from d7e4b2a91c3f.PURGE_FUNCTION (migrations stay self-contained; a test checks).
PREVIOUS_FUNCTION = (
    "CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "BEGIN "
    "IF TG_OP = 'DELETE' THEN "
    "IF current_setting('audit.allow_purge', true) = 'on' "
    "AND OLD.occurred_at < now() - interval '90 days' THEN RETURN OLD; END IF; "
    "END IF; "
    "RAISE EXCEPTION 'audit_log is append-only'; "
    "END $$"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(GUARD_FUNCTION)
    op.execute(STAMP_FUNCTION)
    op.execute(STAMP_TRIGGER)


def downgrade() -> None:
    """Downgrade schema. Drops both guards (looser; never in production)."""
    op.execute("DROP TRIGGER audit_log_stamp_occurred_at ON audit_log")
    op.execute("DROP FUNCTION audit_log_stamp_occurred_at()")
    op.execute(PREVIOUS_FUNCTION)
