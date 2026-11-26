"""usr29 org members read scope

Revision ID: a4c81e6d27f3
Revises: 911eb99ddf8b
Create Date: 2026-11-21 09:00:00.000000

USR-29 (ADR-0025): widens `ck_app_credentials_scope_valid` with the runtime scope
`org-members:read`. No new column or index.

`downgrade()` deletes the `org-members:read` keys first (revocation by removal; the old CHECK
would reject them), then restores the previous list.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c81e6d27f3"
down_revision: str | Sequence[str] | None = "911eb99ddf8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_app_credentials_scope_valid"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(_NAME), "app_credentials", type_="check")
    op.create_check_constraint(
        op.f(_NAME), "app_credentials", "scope IN ('manifest', 'releases', 'org-members:read')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM app_credentials WHERE scope = 'org-members:read'")
    op.drop_constraint(op.f(_NAME), "app_credentials", type_="check")
    op.create_check_constraint(op.f(_NAME), "app_credentials", "scope IN ('manifest', 'releases')")
