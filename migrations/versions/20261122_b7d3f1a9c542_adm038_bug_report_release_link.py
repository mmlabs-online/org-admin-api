"""adm038 bug report release link

Revision ID: b7d3f1a9c542
Revises: a4c81e6d27f3
Create Date: 2026-11-22 09:00:00.000000

ADM-038 (ADR-0028): `bug_reports.release_id` (FK `app_releases.id` ON DELETE SET NULL) and
`release_match` ('reported' | 'inferred' | 'none'), stored at submit. No backfill: existing rows
get `release_id NULL, release_match 'none'`.

`downgrade()` drops the index, both CHECKs, the FK and both columns (reverse order).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d3f1a9c542"
down_revision: str | Sequence[str] | None = "a4c81e6d27f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("bug_reports", sa.Column("release_id", sa.Uuid(), nullable=True))
    op.add_column(
        "bug_reports",
        sa.Column(
            "release_match", sa.String(length=8), server_default=sa.text("'none'"), nullable=False
        ),
    )
    op.create_foreign_key(
        op.f("fk_bug_reports_release_id_app_releases"),
        "bug_reports",
        "app_releases",
        ["release_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_bug_reports_release_match_valid"),
        "bug_reports",
        "release_match IN ('reported', 'inferred', 'none')",
    )
    op.create_check_constraint(
        op.f("ck_bug_reports_release_link_consistent"),
        "bug_reports",
        "release_id IS NULL OR release_match <> 'none'",
    )
    op.create_index(
        "ix_bug_reports_release_id_status",
        "bug_reports",
        ["release_id", "status"],
        postgresql_where=sa.text("release_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_bug_reports_release_id_status",
        table_name="bug_reports",
        postgresql_where=sa.text("release_id IS NOT NULL"),
    )
    op.drop_constraint(op.f("ck_bug_reports_release_link_consistent"), "bug_reports", type_="check")
    op.drop_constraint(op.f("ck_bug_reports_release_match_valid"), "bug_reports", type_="check")
    op.drop_constraint(
        op.f("fk_bug_reports_release_id_app_releases"), "bug_reports", type_="foreignkey"
    )
    op.drop_column("bug_reports", "release_match")
    op.drop_column("bug_reports", "release_id")
