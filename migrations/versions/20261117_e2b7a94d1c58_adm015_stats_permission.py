"""adm015 stats permission

Revision ID: e2b7a94d1c58
Revises: c8a1f4e72d95
Create Date: 2026-11-17 21:00:00.000000

ADM-015: expand-only, data only. Adds the admin permission `admin:stats:read` (`is_admin`, granted
to no role), `ON CONFLICT DO NOTHING`.

`downgrade()` deletes the key (its `role_permissions` rows cascade, which is lossy for roles a
super-admin added it to).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2b7a94d1c58"
down_revision: str | Sequence[str] | None = "c8a1f4e72d95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_APP_ID = "00000000-0000-4000-8000-0000000000a1"
PERMISSIONS = [
    ("admin:stats:read", "Read dashboard statistics (counts; also lists every app key and name)")
]


def upgrade() -> None:
    """Upgrade schema."""
    insert = sa.text(
        "INSERT INTO permissions (id, app_id, key, description, is_admin) "
        "VALUES (gen_random_uuid(), CAST(:app_id AS uuid), :key, :description, true) "
        "ON CONFLICT (app_id, key) DO NOTHING"
    )
    for key, description in PERMISSIONS:
        op.execute(insert.bindparams(app_id=ADMIN_APP_ID, key=key, description=description))


def downgrade() -> None:
    """Downgrade schema (deletes the permission; role_permissions rows cascade)."""
    delete = sa.text(
        "DELETE FROM permissions WHERE app_id = CAST(:app_id AS uuid) AND key IN :keys"
    ).bindparams(sa.bindparam("keys", expanding=True))
    op.execute(delete.bindparams(app_id=ADMIN_APP_ID, keys=[k for k, _ in PERMISSIONS]))
