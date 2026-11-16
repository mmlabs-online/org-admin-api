"""admin permissions is_admin

Revision ID: a7c3e5f19b42
Revises: e91b6c3d58a2
Create Date: 2026-11-15 18:00:00.000000

Data only (ADM-002d): every permission of app `admin` becomes `is_admin = true`. While the
ADM-002b API ran, an admin-app permission could be created or patched with `is_admin = false`;
the service now forces it, and this backfill closes the gap for rows already stored.

Idempotent (only rows with `NOT is_admin` change). Safe: the admin app never has a default role
(`AppService` invariant), and every admin-app role is already privileged, so neither the
default-role guard nor G3 applies. Writes no audit row: a migration is not an admin action
(same as `5e2a9c7d1f04`).

R2 exception: `downgrade()` is a deliberate no-op. The previous per-row values are unknown, and
restoring `false` would reopen the gap.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e5f19b42"
down_revision: str | Sequence[str] | None = "e91b6c3d58a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        sa.text(
            "UPDATE permissions SET is_admin = true "
            "WHERE app_id = (SELECT id FROM apps WHERE key = 'admin') AND NOT is_admin"
        )
    )


def downgrade() -> None:
    """Downgrade schema: deliberate no-op (irreversible data fix, see the module docstring)."""
