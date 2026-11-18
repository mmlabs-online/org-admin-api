"""usr15 join requests cancelled

Revision ID: c7a3e91f5d28
Revises: e2b7a94d1c58
Create Date: 2026-11-18 09:00:00.000000

USR-15: widens `ck_join_requests_status_valid` with `cancelled` (set when a user is disabled while
pending). Data step: pending requests of already-disabled users become `cancelled`
(`decided_at = now()`, `decided_by` NULL); no audit rows (a deploy step, not an admin action).
`downgrade()` is lossy: cancelled rows are deleted (mapping them to `rejected` would create
false cooldowns).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a3e91f5d28"
down_revision: str | Sequence[str] | None = "e2b7a94d1c58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_join_requests_status_valid"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(_NAME), "join_requests", type_="check")
    op.create_check_constraint(
        op.f(_NAME),
        "join_requests",
        "status IN ('pending', 'approved', 'rejected', 'cancelled')",
    )
    op.execute(
        "UPDATE join_requests SET status = 'cancelled', decided_at = now() "
        "WHERE status = 'pending' AND user_id IN (SELECT id FROM users WHERE status = 'disabled')"
    )


def downgrade() -> None:
    """Downgrade schema. Lossy: cancelled rows are deleted."""
    op.execute("DELETE FROM join_requests WHERE status = 'cancelled'")
    op.drop_constraint(op.f(_NAME), "join_requests", type_="check")
    op.create_check_constraint(
        op.f(_NAME), "join_requests", "status IN ('pending', 'approved', 'rejected')"
    )
