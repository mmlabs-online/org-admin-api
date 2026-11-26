"""usr33 users display name length

Revision ID: d4a8e2c6f175
Revises: c91e4b7a3d58
Create Date: 2026-11-25 12:00:00.000000

USR-33 (D40): `ck_users_display_name_length` (NULL or 1-100 characters). The data step turns a
blank name into NULL first (a safety net: no write path existed before this ticket).

`downgrade()` drops the constraint. The data step is not reversed (a blank name carried no
information).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a8e2c6f175"
down_revision: str | Sequence[str] | None = "c91e4b7a3d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "display_name_length"  # expands to ck_users_display_name_length (naming convention)


def upgrade() -> None:
    op.execute(
        "UPDATE users SET display_name = NULL "
        "WHERE display_name IS NOT NULL AND char_length(btrim(display_name)) = 0"
    )
    op.create_check_constraint(
        "display_name_length",
        "users",
        "display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 100",
    )


def downgrade() -> None:
    op.drop_constraint(_CK, "users", type_="check")
