"""create audit log

Revision ID: c3f1a9d27b60
Revises: 5e2a9c7d1f04
Create Date: 2026-11-15 12:00:00.000000

Append-only `audit_log` (ADM-004, ADR-0009): no FKs (actor, app and org are snapshots that
outlive deletes), a trigger that refuses UPDATE, DELETE and TRUNCATE (written by hand: `alembic
check` cannot see triggers or functions), and the `admin:audit:read` permission on app `admin`
(`is_admin=true`, granted to no role).

`downgrade()` destroys the audit log: production never downgrades past this revision. It refuses
offline and while `audit_log` has any row (ADM-004d), like `af3eb84d2d5e`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "c3f1a9d27b60"
down_revision: str | Sequence[str] | None = "5e2a9c7d1f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_APP_ID = "00000000-0000-4000-8000-0000000000a1"
PERMISSION = ("admin:audit:read", "Read the audit log")


def upgrade() -> None:
    """Upgrade schema."""
    # CHECK texts must match app/modules/audit/models.py.
    op.create_table(
        "audit_log",
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("app_key", sa.Text(), nullable=True),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'system', 'anonymous')",
            name=op.f("ck_audit_log_actor_kind_valid"),
        ),
        sa.CheckConstraint(
            "((actor_kind = 'user') = (actor_user_id IS NOT NULL))"
            " AND (actor_kind <> 'system' OR actor_label IS NOT NULL)",
            name=op.f("ck_audit_log_actor_consistent"),
        ),
        sa.CheckConstraint(
            "actor_label IS NULL OR length(actor_label) <= 64",
            name=op.f("ck_audit_log_actor_label_length"),
        ),
        sa.CheckConstraint(
            r"action ~ '^[a-z_]+(\.[a-z_]+)+$' AND length(action) <= 64",
            name=op.f("ck_audit_log_action_format"),
        ),
        sa.CheckConstraint(
            "(target_type IS NULL) = (target_id IS NULL)", name=op.f("ck_audit_log_target_pair")
        ),
        sa.CheckConstraint(
            "app_key IS NULL OR app_key ~ '^[a-z][a-z0-9-]{1,31}$'",
            name=op.f("ck_audit_log_app_key_format"),
        ),
        sa.CheckConstraint(
            "changes IS NULL OR jsonb_typeof(changes) = 'object'",
            name=op.f("ck_audit_log_changes_object"),
        ),
        sa.CheckConstraint(
            "request_id IS NULL OR request_id ~ '^[A-Za-z0-9._-]{1,64}$'",
            name=op.f("ck_audit_log_request_id_format"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    # Ascending composites: a backward scan serves `ORDER BY occurred_at DESC, id DESC`.
    op.create_index("ix_audit_log_occurred_at_id", "audit_log", ["occurred_at", "id"])
    op.create_index(
        "ix_audit_log_actor_user_id",
        "audit_log",
        ["actor_user_id", "occurred_at", "id"],
        postgresql_where=sa.text("actor_user_id IS NOT NULL"),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action", "occurred_at", "id"])
    op.create_index(
        "ix_audit_log_target",
        "audit_log",
        ["target_type", "target_id", "occurred_at", "id"],
        postgresql_where=sa.text("target_id IS NOT NULL"),
    )
    op.create_index(
        "ix_audit_log_app_key",
        "audit_log",
        ["app_key", "occurred_at", "id"],
        postgresql_where=sa.text("app_key IS NOT NULL"),
    )

    # Append-only: binds the owner role too; only DDL defeats it (ADR-0009).
    op.execute(
        "CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable()"
    )

    insert = sa.text(
        "INSERT INTO permissions (id, app_id, key, description, is_admin) "
        "VALUES (gen_random_uuid(), CAST(:app_id AS uuid), :key, :description, true) "
        "ON CONFLICT (app_id, key) DO NOTHING"
    )
    op.execute(insert.bindparams(app_id=ADMIN_APP_ID, key=PERMISSION[0], description=PERMISSION[1]))


AUDIT_LOG_LOSS = (
    "downgrade of c3f1a9d27b60 is refused: audit_log has rows (it would destroy the audit log)"
)


def _refuse_if_rows(bind: Connection) -> None:
    """Raises while `audit_log` has any row (purge trail rows included)."""
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM audit_log)")):
        raise RuntimeError(AUDIT_LOG_LOSS)


def downgrade() -> None:
    """Downgrade schema. Destroys the audit log.

    Refuses offline (`--sql` cannot check the rows) and while any row exists, unless the
    programmatic `allow_audit_log_loss` config attribute is set (tests only; the `alembic` CLI
    cannot set it).
    """
    if context.is_offline_mode():
        raise RuntimeError("offline downgrade of c3f1a9d27b60 is refused: it cannot check rows")
    if not context.config.attributes.get("allow_audit_log_loss"):
        _refuse_if_rows(op.get_bind())
    op.execute(
        sa.text(
            "DELETE FROM permissions WHERE app_id = CAST(:app_id AS uuid) AND key = :key"
        ).bindparams(app_id=ADMIN_APP_ID, key=PERMISSION[0])
    )
    op.execute("DROP TRIGGER audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION audit_log_immutable()")
    op.drop_index("ix_audit_log_app_key", table_name="audit_log")
    op.drop_index("ix_audit_log_target", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_user_id", table_name="audit_log")
    op.drop_index("ix_audit_log_occurred_at_id", table_name="audit_log")
    op.drop_table("audit_log")
