"""seed admin permissions

Revision ID: 5e2a9c7d1f04
Revises: 4d4b5d67dc44
Create Date: 2026-11-15 09:00:00.000000

Data only (ADM-002): the admin leaf permissions that `require_permission` declares on the admin
routes, on app `admin`, all `is_admin=true` (staff powers). Not granted to any role:
`admin:super-admin` keeps only `admin:*`. `ON CONFLICT DO NOTHING`, because local/staging
databases may already hold some of these keys (created while the admin API flags were on).

Known limitation: `downgrade()` also deletes same-key rows that existed before the upgrade
(local/staging only); their `role_permissions` rows cascade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e2a9c7d1f04"
down_revision: str | Sequence[str] | None = "4d4b5d67dc44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_APP_ID = "00000000-0000-4000-8000-0000000000a1"
PERMISSIONS = [
    ("admin:organizations:read", "Read organizations"),
    ("admin:organizations:write", "Create, update and delete organizations"),
    ("admin:users:read", "Read users and their role assignments"),
    ("admin:users:write", "Enable or disable users"),
    ("admin:apps:read", "Read apps and their permissions"),
    ("admin:apps:write", "Register, update and delete apps"),
    ("admin:roles:manage", "Manage permissions and roles of apps"),
    ("admin:roles:assign", "Grant and revoke role assignments"),
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
    """Downgrade schema."""
    delete = sa.text(
        "DELETE FROM permissions WHERE app_id = CAST(:app_id AS uuid) AND key IN :keys"
    ).bindparams(sa.bindparam("keys", expanding=True))
    op.execute(delete.bindparams(app_id=ADMIN_APP_ID, keys=[k for k, _ in PERMISSIONS]))
