"""adm034 app credential scope

Revision ID: 58a494f0449e
Revises: d7a3f5b2e801
Create Date: 2026-11-20 12:00:00.000000

ADM-034 (ADR-0021 §1): every app credential carries exactly one scope. Existing keys become
`manifest` (the column default). The CHECK allows `manifest` and `releases`; ADM-042 drops and
recreates `ck_app_credentials_scope_valid` to add the ADR-0022 runtime scopes. The partial index
of active keys becomes per (app, scope) and serves the "at most 2 active" count.

`downgrade()` first deletes every non-`manifest` credential (data is lost): otherwise `releases`
keys would silently become `manifest` keys, a privilege escalation that could also break the
2-per-app limit. Then the old index comes back and the CHECK and the column are dropped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "58a494f0449e"
down_revision: str | Sequence[str] | None = "d7a3f5b2e801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "app_credentials",
        sa.Column("scope", sa.Text(), server_default=sa.text("'manifest'"), nullable=False),
    )
    op.create_check_constraint(
        "scope_valid", "app_credentials", "scope IN ('manifest', 'releases')"
    )
    op.drop_index(
        "ix_app_credentials_app_id_active",
        table_name="app_credentials",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_app_credentials_app_id_scope_active",
        "app_credentials",
        ["app_id", "scope"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema (deletes every non-manifest credential)."""
    op.execute("DELETE FROM app_credentials WHERE scope <> 'manifest'")
    op.drop_index(
        "ix_app_credentials_app_id_scope_active",
        table_name="app_credentials",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_app_credentials_app_id_active",
        "app_credentials",
        ["app_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_constraint("scope_valid", "app_credentials", type_="check")
    op.drop_column("app_credentials", "scope")
