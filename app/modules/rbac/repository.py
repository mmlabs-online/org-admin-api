import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Select,
    and_,
    delete,
    exists,
    func,
    not_,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.listing import ilike_any, sort_clauses
from app.modules.entitlements.models import OrgEntitlement
from app.modules.organizations.models import Organization, OrganizationStatus
from app.modules.rbac.models import (
    ADMIN_APP_KEY,
    App,
    AppStatus,
    JoinPolicy,
    OrgMode,
    Permission,
    PermissionScope,
    Role,
    RoleAssignment,
    RolePermission,
)
from app.modules.rbac.schemas import AppSort
from app.modules.users.models import User, UserStatus

_APP_SORT_COLUMNS = {"key": App.key, "name": App.name, "created_at": App.created_at}

# (assignment, app key, role key, active) — `active` is computed with the database clock.
AssignmentRow = tuple[RoleAssignment, str, str, bool]
# (assignment, app key, role key, active, holder email, holder display name, holder status)
RoleHolderRow = tuple[RoleAssignment, str, str, bool, str, str | None, str]

# `session.info` flag: an app was written; the CORS origin cache is invalidated after commit.
APPS_CHANGED_KEY = "rbac_apps_changed"


class RbacRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_app_by_key(self, key: str) -> App | None:
        return await self.session.scalar(select(App).where(App.key == key))

    async def active_app_origins_by_key(self) -> dict[str, list[str]]:
        result = await self.session.execute(
            select(App.key, App.origins).where(App.status == AppStatus.ACTIVE.value)
        )
        return {key: list(origins) for key, origins in result}

    async def app_keys_holding_origins(self, origins: list[str], exclude_key: str) -> list[str]:
        """Keys of other apps (any status, `admin` included) holding any of `origins`."""
        if not origins:
            return []
        result = await self.session.scalars(
            select(App.key)
            .where(App.origins.overlap(origins), App.key != exclude_key)
            .order_by(App.key)
        )
        return list(result)

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

    async def list_apps(
        self,
        *,
        status: AppStatus | None,
        join_policy: JoinPolicy | None,
        org_mode: OrgMode | None,
        q: str | None,
        sort: AppSort,
        limit: int,
        offset: int,
    ) -> tuple[list[App], int]:
        conditions: list[ColumnElement[bool]] = []
        if status is not None:
            conditions.append(App.status == status.value)
        if join_policy is not None:
            conditions.append(App.join_policy == join_policy.value)
        if org_mode is not None:
            conditions.append(App.org_mode == org_mode.value)
        if q is not None:
            conditions.append(ilike_any(q, App.key, App.name))
        total = await self.session.scalar(select(func.count()).select_from(App).where(*conditions))
        result = await self.session.scalars(
            select(App)
            .where(*conditions)
            .order_by(*sort_clauses(sort, _APP_SORT_COLUMNS, App.id))
            .limit(limit)
            .offset(offset)
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

    async def app_has_org_assignments(self, app_id: uuid.UUID) -> bool:
        stmt = select(
            exists()
            .where(RoleAssignment.role_id == Role.id)
            .where(Role.app_id == app_id)
            .where(RoleAssignment.org_id.is_not(None))
        )
        return bool(await self.session.scalar(stmt))

    async def app_has_entitlements(self, app_id: uuid.UUID) -> bool:
        stmt = select(exists().where(OrgEntitlement.app_id == app_id))
        return bool(await self.session.scalar(stmt))

    async def set_default_role_if_null(self, app_id: uuid.UUID, role_id: uuid.UUID) -> bool:
        """Conditional update: never overwrites a default set concurrently (no lost update).

        Also never makes the default role the org owner role (ADR-0015). Returns whether it wrote.
        """
        result = await self.session.execute(
            update(App)
            .where(
                App.id == app_id,
                App.default_role_id.is_(None),
                App.org_owner_role_id.is_distinct_from(role_id),
            )
            .values(default_role_id=role_id)
            .execution_options(synchronize_session=False)
        )
        return int(cast(CursorResult[Any], result).rowcount) == 1

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

    async def role_has_org_permission(self, role_id: uuid.UUID) -> bool:
        stmt = select(
            exists()
            .where(RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id, Permission.scope == PermissionScope.ORG.value)
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

    async def page_roles(
        self, app_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Role], int]:
        total = await self.session.scalar(
            select(func.count()).select_from(Role).where(Role.app_id == app_id)
        )
        result = await self.session.scalars(
            select(Role)
            .where(Role.app_id == app_id)
            .order_by(Role.key, Role.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result), total or 0

    async def role_permission_rows(
        self, role_ids: Sequence[uuid.UUID]
    ) -> list[tuple[uuid.UUID, str, bool]]:
        """(role id, permission key, is_admin) for all given roles in one query."""
        if not role_ids:
            return []
        result = await self.session.execute(
            select(RolePermission.role_id, Permission.key, Permission.is_admin)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(RolePermission.role_id.in_(role_ids))
        )
        return [(rid, key, is_admin) for rid, key, is_admin in result.all()]

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

    async def permission_held_by_assigned_role(self, permission_id: uuid.UUID) -> bool:
        """Some role holding the permission has an assignment (any scope, expired included)."""
        stmt = select(
            exists()
            .where(RolePermission.permission_id == permission_id)
            .where(RoleAssignment.role_id == RolePermission.role_id)
        )
        return bool(await self.session.scalar(stmt))

    async def role_holds_permission(self, role_id: uuid.UUID, permission_id: uuid.UUID) -> bool:
        stmt = select(
            exists().where(
                RolePermission.role_id == role_id, RolePermission.permission_id == permission_id
            )
        )
        return bool(await self.session.scalar(stmt))

    async def list_permissions(
        self, app_id: uuid.UUID, *, is_admin: bool | None = None, scope: str | None = None
    ) -> list[Permission]:
        stmt = select(Permission).where(Permission.app_id == app_id).order_by(Permission.key)
        if is_admin is not None:
            stmt = stmt.where(Permission.is_admin.is_(is_admin))
        if scope is not None:
            stmt = stmt.where(Permission.scope == scope)
        return list(await self.session.scalars(stmt))

    async def role_permission_keys(self, role_id: uuid.UUID) -> dict[uuid.UUID, str]:
        result = await self.session.execute(
            select(Permission.id, Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        return {pid: key for pid, key in result.all()}

    async def roles_holding(
        self, app_id: uuid.UUID, permission_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """Permission id -> sorted keys of the roles of the app that hold it."""
        ids = list(permission_ids)
        held: dict[uuid.UUID, list[str]] = {}
        if not ids:
            return held
        result = await self.session.execute(
            select(RolePermission.permission_id, Role.key)
            .join(Role, Role.id == RolePermission.role_id)
            .where(Role.app_id == app_id, RolePermission.permission_id.in_(ids))
            .order_by(Role.key)
        )
        for pid, role_key in result.all():
            held.setdefault(pid, []).append(role_key)
        return held

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

    async def org_role_keys(self, user_id: uuid.UUID, app_key: str, org_id: uuid.UUID) -> list[str]:
        """Role keys held in exactly `org_id` (never the app-scope union): unexpired, and only
        while the org is `active`. Empty for an unknown, suspended or not-a-member org."""
        stmt = (
            select(Role.key)
            .distinct()
            .select_from(RoleAssignment)
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(App, App.id == Role.app_id)
            .join(Organization, Organization.id == RoleAssignment.org_id)
            .where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.org_id == org_id,
                App.key == app_key,
                Organization.status == OrganizationStatus.ACTIVE.value,
                # Database clock, never the Python one.
                or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now()),
            )
            .order_by(Role.key)
        )
        return list(await self.session.scalars(stmt))

    async def org_permission_keys(
        self, user_id: uuid.UUID, app_key: str, org_id: uuid.UUID
    ) -> list[str]:
        """Permission keys of the roles held in exactly `org_id` (never the app-scope union),
        with the same rules as `org_role_keys`."""
        stmt = (
            select(Permission.key)
            .distinct()
            .select_from(RoleAssignment)
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(App, App.id == Role.app_id)
            .join(Organization, Organization.id == RoleAssignment.org_id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(
                Permission,
                and_(Permission.id == RolePermission.permission_id, Permission.app_id == App.id),
            )
            .where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.org_id == org_id,
                App.key == app_key,
                Organization.status == OrganizationStatus.ACTIVE.value,
                or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now()),
            )
            .order_by(Permission.key)
        )
        return list(await self.session.scalars(stmt))

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
    def _is_active() -> ColumnElement[bool]:
        """Not expired, with the database clock (the single definition of `active`)."""
        return or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now())

    @staticmethod
    def _assignment_rows() -> Select[RoleAssignment, str, str, bool]:
        return (
            select(RoleAssignment, App.key, Role.key, RbacRepository._is_active().label("active"))
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(App, App.id == Role.app_id)
        )

    async def page_role_holders(
        self,
        role_id: uuid.UUID,
        *,
        active: bool | None,
        org_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[RoleHolderRow], int]:
        """Two statements (count + rows). Explicit `User` columns: never the entity, so
        `password_hash` cannot reach the result."""
        filters: list[ColumnElement[bool]] = [RoleAssignment.role_id == role_id]
        if active is True:
            filters.append(self._is_active())
        elif active is False:
            filters.append(not_(self._is_active()))
        if org_id is not None:
            filters.append(RoleAssignment.org_id == org_id)
        total = await self.session.scalar(
            select(func.count()).select_from(RoleAssignment).where(*filters)
        )
        result = await self.session.execute(
            self._assignment_rows()
            .add_columns(User.email, User.display_name, User.status)
            .join(User, User.id == RoleAssignment.user_id)
            .where(*filters)
            .order_by(RoleAssignment.created_at.desc(), RoleAssignment.id)
            .limit(limit)
            .offset(offset)
        )
        return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in result.all()], total or 0

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

    async def user_has_privileged_assignment(self, user_id: uuid.UUID) -> bool:
        """Any assignment (expired included: it can be renewed) of an admin-app role or of a role
        holding an `is_admin` permission."""
        holds_admin_permission = (
            exists()
            .where(RolePermission.role_id == Role.id)
            .where(Permission.id == RolePermission.permission_id, Permission.is_admin.is_(True))
        )
        stmt = select(
            exists()
            .where(RoleAssignment.user_id == user_id)
            .where(Role.id == RoleAssignment.role_id, App.id == Role.app_id)
            .where(or_(App.key == ADMIN_APP_KEY, holds_admin_permission))
        )
        return bool(await self.session.scalar(stmt))

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
        """App scope, not expired (database clock), held by an `active`, verified user (only
        such a user can get an admin token, D16)."""
        return and_(
            RoleAssignment.role_id == role_id,
            RoleAssignment.org_id.is_(None),
            or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now()),
            User.id == RoleAssignment.user_id,
            User.status == UserStatus.ACTIVE.value,
            User.email_verified_at.is_not(None),
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

    async def list_app_summaries(self) -> list[tuple[uuid.UUID, str, str, str]]:
        """(id, key, name, status) of every app, ordered by key."""
        result = await self.session.execute(
            select(App.id, App.key, App.name, App.status).order_by(App.key)
        )
        return [(r[0], r[1], r[2], r[3]) for r in result]

    async def member_counts(
        self, app_ids: Sequence[uuid.UUID] | None = None
    ) -> dict[uuid.UUID, int]:
        """OQ5 member definition (owner, 2026-11-17): distinct `active` users with at least one
        non-expired role assignment on the app (app scope or any org). ADM-015 dashboard and
        ADM-016 `member_count` both call this; do not duplicate it. Apps without members are
        absent from the result (callers default to 0)."""
        if app_ids is not None and not app_ids:
            return {}
        stmt = (
            select(Role.app_id, func.count(func.distinct(RoleAssignment.user_id)))
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(
                User,
                and_(User.id == RoleAssignment.user_id, User.status == UserStatus.ACTIVE.value),
            )
            .where(or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now()))
            .group_by(Role.app_id)
        )
        if app_ids is not None:
            stmt = stmt.where(Role.app_id.in_(app_ids))
        return {app_id: total for app_id, total in await self.session.execute(stmt)}
