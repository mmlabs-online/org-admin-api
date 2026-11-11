"""Authorization read path: effective roles/permissions and the permission matcher (ADR-0003)."""

import uuid
from collections.abc import Collection

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.repository import RbacRepository


def permits(granted: Collection[str], required: str) -> bool:
    """True if `granted` holds `required` exactly or the wildcard `<app>:*` of its app.

    No global `*` and no `<app>:<resource>:*`. Kept free of ORM imports: ADM-002 and the
    `mmlabs-auth` library copy it as is.
    """
    app, sep, rest = required.partition(":")
    if not sep or not app or not rest:
        return False
    return required in granted or f"{app}:*" in granted


class AuthzService:
    """RBAC only.

    Does not check user status, email verification, or app status/join policy; callers
    (USR-03 token, ADM-002) check those and return their own errors.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)

    async def effective_roles(
        self, user_id: uuid.UUID, app_key: str, org_id: uuid.UUID | None = None
    ) -> list[str]:
        """Sorted role keys: app-scope roles, plus roles in `org_id` when given; expired ignored."""
        return await self.repo.effective_role_keys(user_id, app_key, org_id)

    async def effective_permissions(
        self, user_id: uuid.UUID, app_key: str, org_id: uuid.UUID | None = None
    ) -> list[str]:
        """Sorted permission keys of the effective roles; `<app>:*` is returned unexpanded."""
        keys = await self.repo.effective_permission_keys(user_id, app_key, org_id)
        # Second check: drop keys registered under another app's prefix.
        prefix = f"{app_key}:"
        return [k for k in keys if k.startswith(prefix)]
