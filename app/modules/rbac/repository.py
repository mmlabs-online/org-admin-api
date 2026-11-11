import uuid
from collections.abc import Iterable

from sqlalchemy import ColumnElement, Select, and_, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, Permission, Role, RoleAssignment, RolePermission


class RbacRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_app_by_key(self, key: str) -> App | None:
        return await self.session.scalar(select(App).where(App.key == key))

    async def get_role_by_key(self, app_id: uuid.UUID, key: str) -> Role | None:
        return await self.session.scalar(select(Role).where(Role.app_id == app_id, Role.key == key))

    async def add_role(self, role: Role) -> Role:
        self.session.add(role)
        await self.session.flush()
        await self.session.refresh(role)
        return role

    async def list_permissions(
        self, app_id: uuid.UUID, *, is_admin: bool | None = None
    ) -> list[Permission]:
        stmt = select(Permission).where(Permission.app_id == app_id).order_by(Permission.key)
        if is_admin is not None:
            stmt = stmt.where(Permission.is_admin.is_(is_admin))
        return list(await self.session.scalars(stmt))

    async def role_permission_keys(self, role_id: uuid.UUID) -> dict[uuid.UUID, str]:
        result = await self.session.execute(
            select(Permission.id, Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        return {pid: key for pid, key in result.all()}

    async def add_role_permissions(
        self, role_id: uuid.UUID, app_id: uuid.UUID, permission_ids: Iterable[uuid.UUID]
    ) -> None:
        rows = [{"role_id": role_id, "permission_id": p, "app_id": app_id} for p in permission_ids]
        if rows:
            await self.session.execute(insert(RolePermission), rows)

    async def remove_role_permissions(
        self, role_id: uuid.UUID, permission_ids: Iterable[uuid.UUID]
    ) -> None:
        ids = list(permission_ids)
        if ids:
            await self.session.execute(
                delete(RolePermission).where(
                    RolePermission.role_id == role_id, RolePermission.permission_id.in_(ids)
                )
            )

    @staticmethod
    def _active_assignments(
        stmt: Select[str], user_id: uuid.UUID, app_key: str, org_id: uuid.UUID | None
    ) -> Select[str]:
        scope: ColumnElement[bool] = (
            RoleAssignment.org_id.is_(None)
            if org_id is None
            else or_(RoleAssignment.org_id.is_(None), RoleAssignment.org_id == org_id)
        )
        return stmt.where(
            RoleAssignment.user_id == user_id,
            App.key == app_key,
            scope,
            # Database clock, never the Python one.
            or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now()),
        )

    async def effective_role_keys(
        self, user_id: uuid.UUID, app_key: str, org_id: uuid.UUID | None
    ) -> list[str]:
        stmt = (
            select(Role.key)
            .distinct()
            .select_from(RoleAssignment)
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(App, App.id == Role.app_id)
            .order_by(Role.key)
        )
        return list(
            await self.session.scalars(self._active_assignments(stmt, user_id, app_key, org_id))
        )

    async def effective_permission_keys(
        self, user_id: uuid.UUID, app_key: str, org_id: uuid.UUID | None
    ) -> list[str]:
        stmt = (
            select(Permission.key)
            .distinct()
            .select_from(RoleAssignment)
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(App, App.id == Role.app_id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(
                Permission,
                and_(Permission.id == RolePermission.permission_id, Permission.app_id == App.id),
            )
            .order_by(Permission.key)
        )
        return list(
            await self.session.scalars(self._active_assignments(stmt, user_id, app_key, org_id))
        )
