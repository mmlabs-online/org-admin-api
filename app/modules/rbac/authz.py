"""Authorization read path: effective roles/permissions and the permission matcher (ADR-0003)."""

import uuid
from collections.abc import Sequence
from collections.abc import Set as AbstractSet

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import ADMIN_APP_KEY, App, AppStatus, OrgMode
from app.modules.rbac.repository import RbacRepository


def permits(granted: AbstractSet[str] | Sequence[str], required: str) -> bool:
    """True if `granted` holds `required` exactly or the wildcard `<app>:*` of its app.

    No global `*` and no `<app>:<resource>:*`. Kept free of ORM imports: ADM-002 and the
    `mmlabs-auth` library copy it as is.
    """
    if isinstance(granted, str):  # `in` on a str would match substrings
        raise TypeError("granted must be a collection of keys, not a str")
    app, sep, rest = required.partition(":")
    if not sep or not app or not rest:
        return False
    return required in granted or f"{app}:*" in granted


def is_designated_super_admin_role(app: App, role_id: uuid.UUID) -> bool:
    """True if `role_id` is the app's designated super-admin role (USR-38, ADR-0030).

    The one Python predicate for "privileged by designation"; USR-39 and USR-40 import it.
    """
    return app.super_admin_role_id is not None and app.super_admin_role_id == role_id


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
        keys = await self.repo.effective_role_keys(user_id, app_key, org_id)
        # Second check: drop keys registered under another app's prefix.
        prefix = f"{app_key}:"
        return [k for k in keys if k.startswith(prefix)]

    async def org_role_keys(self, user_id: uuid.UUID, app_key: str, org_id: uuid.UUID) -> list[str]:
        """Sorted role keys held in exactly this org (org-exact, unexpired, org `active`)."""
        keys = await self.repo.org_role_keys(user_id, app_key, org_id)
        prefix = f"{app_key}:"
        return [k for k in keys if k.startswith(prefix)]

    async def org_permissions(
        self, user_id: uuid.UUID, app_key: str, org_id: uuid.UUID
    ) -> list[str]:
        """Sorted permission keys held in exactly this org (org-exact, unexpired, org `active`)."""
        keys = await self.repo.org_permission_keys(user_id, app_key, org_id)
        prefix = f"{app_key}:"
        return [k for k in keys if k.startswith(prefix)]

    async def effective_permissions(
        self, user_id: uuid.UUID, app_key: str, org_id: uuid.UUID | None = None
    ) -> list[str]:
        """Sorted permission keys of the effective roles; `<app>:*` is returned unexpanded."""
        keys = await self.repo.effective_permission_keys(user_id, app_key, org_id)
        # Second check: drop keys registered under another app's prefix.
        prefix = f"{app_key}:"
        return [k for k in keys if k.startswith(prefix)]

    async def app_super_admin_access(self, user_id: uuid.UUID, app: App) -> bool:
        """True if the user holds the app's designated super-admin role right now (ADR-0030).

        Plain reads, no lock. A live staff `admin:super-admin` is required as well. The holder
        predicate for USR-39 (mint) and USR-40 (picker).
        """
        if (
            app.key == ADMIN_APP_KEY
            or app.status != AppStatus.ACTIVE.value
            or app.org_mode != OrgMode.REQUIRED.value
            or app.super_admin_role_id is None
        ):
            return False
        return await self.repo.app_super_admin_access(user_id, app.super_admin_role_id)

    async def is_org_of_app(self, org_id: uuid.UUID, app_id: uuid.UUID) -> bool:
        """OQ89 (b): active org with an assignment or an enabled entitlement of the app."""
        return await self.repo.is_org_of_app(org_id, app_id)
