from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.rbac.models import Role
from app.modules.rbac.repository import RbacRepository


@dataclass(frozen=True)
class DefaultRoleResult:
    role_key: str
    created: bool
    added: list[str]
    removed: list[str]


class RoleCatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)

    async def build_default_role(self, app_key: str) -> DefaultRoleResult:
        """Create or update `<app>:member` = every permission of the app with `is_admin = false`.

        Runs only when called (seed, app creation in USR-02b, USR-05 manifest), never
        automatically on permission changes: that would silently widen or undo manual
        tightening (D9: role content is data). `<app>:*` is never included (the CHECK forces
        it to `is_admin`). Sets `apps.default_role_id` only when it is NULL. Idempotent.
        """
        # R4: audit entry once ADM-004 lands.
        app = await self.repo.get_app_by_key(app_key)
        if app is None:
            raise NotFoundError("App not found")

        key = f"{app.key}:member"
        role = await self.repo.get_role_by_key(app.id, key)
        created = role is None
        if role is None:
            role = await self.repo.add_role(
                Role(app_id=app.id, key=key, name="Member", is_system=True)
            )

        target = {p.id: p.key for p in await self.repo.list_permissions(app.id, is_admin=False)}
        current = await self.repo.role_permission_keys(role.id)
        to_add = target.keys() - current.keys()
        to_remove = current.keys() - target.keys()
        await self.repo.add_role_permissions(role.id, app.id, to_add)
        await self.repo.remove_role_permissions(role.id, to_remove)

        if app.default_role_id is None:
            app.default_role_id = role.id
        await self.repo.session.flush()

        return DefaultRoleResult(
            role_key=key,
            created=created,
            added=sorted(target[p] for p in to_add),
            removed=sorted(current[p] for p in to_remove),
        )
