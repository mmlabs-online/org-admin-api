import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import ColumnElement, Select, and_, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.models import Organization
from app.modules.rbac.models import (
    App,
    AppStatus,
    Permission,
    Role,
    RoleAssignment,
    RolePermission,
)
from app.modules.users.models import User, UserStatus

# (assignment, app key, role key, active) — `active` is computed with the database clock.
AssignmentRow = tuple[RoleAssignment, str, str, bool]

# `session.info` flag: an app was written; the CORS origin cache is invalidated after commit.
APPS_CHANGED_KEY = "rbac_apps_changed"


class RbacRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_app_by_key(self, key: str) -> App | None:
        return await self.session.scalar(select(App).where(App.key == key))

    async def active_app_origins(self) -> list[list[str]]:
        result = await self.session.scalars(
            select(App.origins).where(App.status == AppStatus.ACTIVE.value)
        )
        return [list(origins) for origins in result]

    def mark_apps_changed(self) -> None:
        self.session.info[APPS_CHANGED_KEY] = True

    async def lock_app(self, key: str) -> App | None:
        """Lock the app row (`FOR NO KEY UPDATE`) until the transaction ends.

        Every RBAC writer must call this first (ADR-0003 §9/§10): roles, permissions,
        `role_permissions` and `apps.default_role_id` of one app are then changed one request at
        a time. FK checks (`KEY SHARE`) are not blocked.
        """
        return await self.session.scalar(
            select(App)
            .where(App.key == key)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )

    async def list_apps(self, *, limit: int, offset: int) -> tuple[list[App], int]:
        total = await self.session.scalar(select(func.count()).select_from(App))
        result = await self.session.scalars(
            select(App).order_by(App.key).limit(limit).offset(offset)
        )
        return list(result), total or 0

    async def add_app(self, app: App) -> App:
        """Insert in a savepoint so a unique violation leaves the outer transaction usable."""
        async with self.session.begin_nested():
            self.session.add(app)
            await self.session.flush()
        await self.session.refresh(app)
        return app

    async def save(self) -> None:
        """Flush pending changes in a savepoint (an `IntegrityError` keeps the session usable)."""
        async with self.session.begin_nested():
            await self.session.flush()

    async def delete_app(self, app: App) -> None:
        """Delete in a savepoint; the database cascades the app's roles and permissions."""
        async with self.session.begin_nested():
            await self.session.delete(app)
            await self.session.flush()

    async def app_has_assignments(self, app_id: uuid.UUID) -> bool:
        stmt = select(
            exists().where(RoleAssignment.role_id == Role.id).where(Role.app_id == app_id)
        )
        return bool(await self.session.scalar(stmt))

    async def set_default_role_if_null(self, app_id: uuid.UUID, role_id: uuid.UUID) -> None:
        """Conditional update: never overwrites a default set concurrently (no lost update)."""
        await self.session.execute(
            update(App)
            .where(App.id == app_id, App.default_role_id.is_(None))
            .values(default_role_id=role_id)
            .execution_options(synchronize_session=False)
        )

    async def catalog_rows(self, app_id: uuid.UUID) -> list[tuple[str, str, bool, str | None]]:
        """(role key, name, is_system, permission key or None) for every role of the app.

        One query; unordered (the service sorts, so bytes never depend on DB collation).
        """
        result = await self.session.execute(
            select(Role.key, Role.name, Role.is_system, Permission.key)
            .select_from(Role)
            .outerjoin(RolePermission, RolePermission.role_id == Role.id)
            .outerjoin(Permission, Permission.id == RolePermission.permission_id)
            .where(Role.app_id == app_id)
        )
        return [(r[0], r[1], r[2], r[3]) for r in result.all()]

    async def get_role(self, app_id: uuid.UUID, role_id: uuid.UUID) -> Role | None:
        return await self.session.scalar(
            select(Role).where(Role.app_id == app_id, Role.id == role_id)
        )

    async def get_role_by_key(self, app_id: uuid.UUID, key: str) -> Role | None:
        return await self.session.scalar(select(Role).where(Role.app_id == app_id, Role.key == key))

    async def role_has_admin_permission(self, role_id: uuid.UUID) -> bool:
        stmt = select(
            exists()
            .where(RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id, Permission.is_admin.is_(True))
        )
        return bool(await self.session.scalar(stmt))

    async def add_role(self, role: Role) -> Role:
        """Insert in a savepoint so a unique violation leaves the outer transaction usable."""
        async with self.session.begin_nested():
            self.session.add(role)
            await self.session.flush()
        await self.session.refresh(role)
        return role

    async def delete_role(self, role: Role) -> None:
        """Delete in a savepoint; the database cascades the role's `role_permissions`."""
        async with self.session.begin_nested():
            await self.session.delete(role)
            await self.session.flush()

    async def role_has_assignments(self, role_id: uuid.UUID) -> bool:
        stmt = select(exists().where(RoleAssignment.role_id == role_id))
        return bool(await self.session.scalar(stmt))

    async def page_permissions(
        self, app_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Permission], int]:
        total = await self.session.scalar(
            select(func.count()).select_from(Permission).where(Permission.app_id == app_id)
        )
        result = await self.session.scalars(
            select(Permission)
            .where(Permission.app_id == app_id)
            .order_by(Permission.key)
            .limit(limit)
            .offset(offset)
        )
        return list(result), total or 0

    async def get_permission(
        self, app_id: uuid.UUID, permission_id: uuid.UUID
    ) -> Permission | None:
        return await self.session.scalar(
            select(Permission).where(Permission.app_id == app_id, Permission.id == permission_id)
        )

    async def get_permission_by_key(self, app_id: uuid.UUID, key: str) -> Permission | None:
        return await self.session.scalar(
            select(Permission).where(Permission.app_id == app_id, Permission.key == key)
        )

    async def permissions_by_keys(self, app_id: uuid.UUID, keys: list[str]) -> list[Permission]:
        if not keys:
            return []
        return list(
            await self.session.scalars(
                select(Permission).where(Permission.app_id == app_id, Permission.key.in_(keys))
            )
        )

    async def add_permission(self, permission: Permission) -> Permission:
        """Insert in a savepoint so a unique violation leaves the outer transaction usable."""
        async with self.session.begin_nested():
            self.session.add(permission)
            await self.session.flush()
        await self.session.refresh(permission)
        return permission

    async def delete_permission(self, permission: Permission) -> None:
        """Delete in a savepoint; the database cascades its `role_permissions`."""
        async with self.session.begin_nested():
            await self.session.delete(permission)
            await self.session.flush()

    async def permission_held_by_any_role(self, permission_id: uuid.UUID) -> bool:
        stmt = select(exists().where(RolePermission.permission_id == permission_id))
        return bool(await self.session.scalar(stmt))

    async def role_holds_permission(self, role_id: uuid.UUID, permission_id: uuid.UUID) -> bool:
        stmt = select(
            exists().where(
                RolePermission.role_id == role_id, RolePermission.permission_id == permission_id
            )
        )
        return bool(await self.session.scalar(stmt))

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
            await self.session.execute(
                pg_insert(RolePermission).values(rows).on_conflict_do_nothing()
            )

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

    async def apply_role_permission_diff(
        self,
        role_id: uuid.UUID,
        app_id: uuid.UUID,
        add: Iterable[uuid.UUID],
        remove: Iterable[uuid.UUID],
    ) -> None:
        """Apply the diff in one savepoint (an `IntegrityError` keeps the session usable)."""
        async with self.session.begin_nested():
            await self.add_role_permissions(role_id, app_id, add)
            await self.remove_role_permissions(role_id, remove)

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

    # --- role assignments ---------------------------------------------------

    async def user_exists(self, user_id: uuid.UUID) -> bool:
        return bool(await self.session.scalar(select(exists().where(User.id == user_id))))

    async def role_with_app(self, role_id: uuid.UUID) -> tuple[Role, App] | None:
        """Unlocked read: tells the caller which app to lock."""
        row = (
            await self.session.execute(
                select(Role, App).join(App, App.id == Role.app_id).where(Role.id == role_id)
            )
        ).first()
        return None if row is None else (row[0], row[1])

    async def org_exists_key_share(self, org_id: uuid.UUID) -> bool:
        """`FOR KEY SHARE`: the organization cannot be deleted before this transaction ends."""
        found = await self.session.scalar(
            select(Organization.id)
            .where(Organization.id == org_id)
            .with_for_update(read=True, key_share=True)
        )
        return found is not None

    async def db_now(self) -> datetime:
        return (await self.session.execute(select(func.now()))).scalar_one()

    @staticmethod
    def _assignment_rows() -> Select[RoleAssignment, str, str, bool]:
        active = or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now())
        return (
            select(RoleAssignment, App.key, Role.key, active.label("active"))
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(App, App.id == Role.app_id)
        )

    async def page_assignments(
        self, user_id: uuid.UUID, *, app_key: str | None, limit: int, offset: int
    ) -> tuple[list[AssignmentRow], int]:
        count = (
            select(func.count())
            .select_from(RoleAssignment)
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(App, App.id == Role.app_id)
            .where(RoleAssignment.user_id == user_id)
        )
        rows = self._assignment_rows().where(RoleAssignment.user_id == user_id)
        if app_key is not None:
            count = count.where(App.key == app_key)
            rows = rows.where(App.key == app_key)
        total = await self.session.scalar(count)
        result = await self.session.execute(
            rows.order_by(RoleAssignment.created_at.desc(), RoleAssignment.id)
            .limit(limit)
            .offset(offset)
        )
        return [(r[0], r[1], r[2], r[3]) for r in result.all()], total or 0

    async def get_assignment_row(
        self, user_id: uuid.UUID, assignment_id: uuid.UUID
    ) -> AssignmentRow | None:
        """Scoped to `user_id`: another user's assignment is not found (no leak)."""
        row = (
            await self.session.execute(
                self._assignment_rows()
                .where(RoleAssignment.id == assignment_id, RoleAssignment.user_id == user_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        return None if row is None else (row[0], row[1], row[2], row[3])

    async def find_assignment(
        self, user_id: uuid.UUID, role_id: uuid.UUID, org_id: uuid.UUID | None
    ) -> RoleAssignment | None:
        return await self.session.scalar(
            select(RoleAssignment)
            .where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.role_id == role_id,
                RoleAssignment.org_id.is_not_distinct_from(org_id),
            )
            .execution_options(populate_existing=True)
        )

    async def add_assignment(self, ra: RoleAssignment) -> RoleAssignment:
        """Insert in a savepoint so a unique or FK violation leaves the transaction usable."""
        async with self.session.begin_nested():
            self.session.add(ra)
            await self.session.flush()
        return ra

    async def delete_assignment(self, ra: RoleAssignment) -> None:
        async with self.session.begin_nested():
            await self.session.delete(ra)
            await self.session.flush()

    @staticmethod
    def _counting_super_admin(role_id: uuid.UUID) -> ColumnElement[bool]:
        """App scope, not expired (database clock), held by an `active` user."""
        return and_(
            RoleAssignment.role_id == role_id,
            RoleAssignment.org_id.is_(None),
            or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now()),
            User.id == RoleAssignment.user_id,
            User.status == UserStatus.ACTIVE.value,
        )

    async def count_other_active_super_admins(
        self, role_id: uuid.UUID, exclude_id: uuid.UUID
    ) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(RoleAssignment)
            .join(User, User.id == RoleAssignment.user_id)
            .where(self._counting_super_admin(role_id), RoleAssignment.id != exclude_id)
        )
        return total or 0

    async def is_counting_super_admin(self, role_id: uuid.UUID, assignment_id: uuid.UUID) -> bool:
        stmt = select(
            exists().where(self._counting_super_admin(role_id), RoleAssignment.id == assignment_id)
        )
        return bool(await self.session.scalar(stmt))
