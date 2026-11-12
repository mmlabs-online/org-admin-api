import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.modules.rbac.models import (
    ADMIN_APP_KEY,
    APP_KEY_RE,
    App,
    AppStatus,
    JoinPolicy,
    OrgMode,
    Permission,
    Role,
    RoleAssignment,
)
from app.modules.rbac.repository import AssignmentRow, RbacRepository
from app.modules.rbac.schemas import (
    AppCreate,
    AppUpdate,
    AssignmentCreate,
    AssignmentRead,
    PermissionCreate,
    PermissionUpdate,
    RoleCatalog,
    RoleCatalogEntry,
    RoleCreate,
    RolePermissionsReplace,
    RoleRead,
    RoleUpdate,
)
from app.modules.users.models import UserStatus
from app.modules.users.repository import UserRepository

ADMIN_WILDCARD = f"{ADMIN_APP_KEY}:*"
SUPER_ADMIN_ROLE = f"{ADMIN_APP_KEY}:super-admin"


class BootstrapOutcome(StrEnum):
    GRANTED = "granted"
    ALREADY_HELD = "already_held"
    UNKNOWN_EMAIL = "unknown_email"
    USER_DISABLED = "user_disabled"
    NOT_SEEDED = "not_seeded"


@dataclass(frozen=True)
class BootstrapResult:
    """Plain values for the CLI, so it never touches the ORM."""

    outcome: BootstrapOutcome
    user_id: uuid.UUID | None = None
    email: str | None = None
    created_at: datetime | None = None
    email_verified: bool | None = None


@dataclass(frozen=True)
class DefaultRoleResult:
    role_key: str
    created: bool
    added: list[str]
    removed: list[str]


async def _set_role_permissions(
    repo: RbacRepository, role: Role, target: dict[uuid.UUID, str]
) -> tuple[list[str], list[str]]:
    """Make `role` hold exactly `target` ({permission id: key}); returns (added, removed) keys.

    Shared by the default-role builder and the role PUT. No write when nothing changes; the diff
    is applied in one savepoint and a racing change (e.g. a permission deleted meanwhile) is a 409.
    """
    current = await repo.role_permission_keys(role.id)
    to_add = target.keys() - current.keys()
    to_remove = current.keys() - target.keys()
    if to_add or to_remove:
        try:
            await repo.apply_role_permission_diff(role.id, role.app_id, to_add, to_remove)
        except IntegrityError as exc:
            raise ConflictError("Role permissions changed concurrently") from exc
    return sorted(target[p] for p in to_add), sorted(current[p] for p in to_remove)


def _prefix_mismatch() -> UnprocessableError:
    return UnprocessableError(
        "Key prefix must be the app key",
        details=[{"field": "key", "reason": "prefix_mismatch"}],
    )


def _wildcard_must_be_admin() -> UnprocessableError:
    return UnprocessableError(
        "The app wildcard permission must be is_admin",
        details=[{"field": "is_admin", "reason": "wildcard_must_be_admin"}],
    )


async def _locked_app(repo: RbacRepository, key: str) -> App:
    app = await repo.lock_app(key)
    if app is None:
        raise NotFoundError("App not found")
    return app


class RoleCatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)

    async def read_catalog(self, app_key: str) -> RoleCatalog:
        """Public catalog (D17): unknown, disabled, `admin` and malformed keys are one 404."""
        if re.fullmatch(APP_KEY_RE, app_key) is None:
            raise NotFoundError("App not found")
        app = await self.repo.get_app_by_key(app_key)
        if app is None or app.status != AppStatus.ACTIVE or app.key == ADMIN_APP_KEY:
            raise NotFoundError("App not found")

        roles: dict[str, tuple[str, bool, set[str]]] = {}
        prefix = f"{app.key}:"
        for role_key, name, is_system, perm_key in await self.repo.catalog_rows(app.id):
            entry = roles.setdefault(role_key, (name, is_system, set()))
            if perm_key is not None and perm_key.startswith(prefix):  # ADR-0003 §4
                entry[2].add(perm_key)
        return RoleCatalog(
            app=app.key,
            roles=[
                RoleCatalogEntry(key=key, name=name, is_system=is_system, permissions=sorted(perms))
                for key, (name, is_system, perms) in sorted(roles.items())
            ],
        )

    async def build_default_role(
        self, app_key: str, actor: uuid.UUID | None = None
    ) -> DefaultRoleResult:
        """Create or update `<app>:member` = every permission of the app with `is_admin = false`.

        Runs only when called (seed, app creation, USR-05 manifest), never automatically on
        permission changes: that would silently widen or undo manual tightening (D9: role
        content is data). `<app>:*` is never included (the CHECK forces it to `is_admin`). Sets
        `apps.default_role_id` only when it is NULL. Idempotent. Refuses the `admin` app (409).
        """
        return await self._build_for_app(await _locked_app(self.repo, app_key))

    async def _build_for_app(self, app: App) -> DefaultRoleResult:
        """The caller holds the app lock (`lock_app`) or owns the uncommitted new app row."""
        # R4: audit entry once ADM-004 lands.
        if app.key == ADMIN_APP_KEY:
            raise ConflictError("The admin app has no member role")

        key = f"{app.key}:member"
        role = await self.repo.get_role_by_key(app.id, key)
        created = role is None
        if role is None:
            try:
                role = await self.repo.add_role(
                    Role(app_id=app.id, key=key, name="Member", is_system=True)
                )
            except IntegrityError as exc:
                raise ConflictError("Member role was created concurrently") from exc

        target = {p.id: p.key for p in await self.repo.list_permissions(app.id, is_admin=False)}
        added, removed = await _set_role_permissions(self.repo, role, target)

        await self.repo.set_default_role_if_null(app.id, role.id)
        await self.repo.session.refresh(app)

        return DefaultRoleResult(role_key=key, created=created, added=added, removed=removed)


class AppService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)
        self.catalog = RoleCatalogService(session)

    async def list_apps(self, *, limit: int, offset: int) -> tuple[list[App], int]:
        return await self.repo.list_apps(limit=limit, offset=offset)

    async def get(self, key: str) -> App:
        app = await self.repo.get_app_by_key(key)
        if app is None:
            raise NotFoundError("App not found")
        return app

    async def create(self, data: AppCreate, actor: uuid.UUID | None = None) -> App:
        # R4: audit entry in the same transaction once ADM-004 lands.
        if await self.repo.get_app_by_key(data.key) is not None:
            raise ConflictError("App key is already taken")
        try:
            app = await self.repo.add_app(
                App(
                    key=data.key,
                    name=data.name,
                    join_policy=data.join_policy.value,
                    org_mode=data.org_mode.value,
                    origins=data.origins,
                )
            )
        except IntegrityError as exc:  # concurrent insert with the same key
            raise ConflictError("App key is already taken") from exc
        # Same transaction: a failure here rolls the app back with the request.
        await self.catalog._build_for_app(app)
        self.repo.mark_apps_changed()
        return app

    async def update(self, key: str, data: AppUpdate, actor: uuid.UUID | None = None) -> App:
        app = await _locked_app(self.repo, key)
        fields = data.model_fields_set

        if app.key == ADMIN_APP_KEY and (
            ("join_policy" in fields and data.join_policy != JoinPolicy.CLOSED)
            or ("default_role_id" in fields and data.default_role_id is not None)
            or ("status" in fields and data.status != AppStatus.ACTIVE)
        ):
            raise ConflictError("The admin app must stay active, closed and without a default role")

        if "default_role_id" in fields and data.default_role_id is not None:
            if await self.repo.get_role(app.id, data.default_role_id) is None:
                raise UnprocessableError(
                    "Unknown role for this app",
                    details=[{"field": "default_role_id", "reason": "unknown_role"}],
                )
            if await self.repo.role_has_admin_permission(data.default_role_id):
                raise UnprocessableError(
                    "A default role must not hold admin permissions",
                    details=[{"field": "default_role_id", "reason": "role_has_admin_permissions"}],
                )

        changed = False
        # ADM-002 guard: changing admin origins requires admin:* (applied with the fields below).
        for field in fields:
            value = getattr(data, field)
            if isinstance(value, StrEnum):
                value = value.value
            if getattr(app, field) != value:
                setattr(app, field, value)
                changed = True
        if not changed:
            return app  # no write: updated_at stays unchanged

        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            await self.repo.save()
        except IntegrityError as exc:  # e.g. the role was deleted concurrently
            raise ConflictError("App was changed concurrently") from exc
        self.repo.mark_apps_changed()
        await self.repo.session.refresh(app)
        return app

    async def delete(self, key: str, actor: uuid.UUID | None = None) -> None:
        app = await _locked_app(self.repo, key)
        if app.key == ADMIN_APP_KEY:
            raise ConflictError("The admin app cannot be deleted")
        if await self.repo.app_has_assignments(app.id):
            raise ConflictError("App has role assignments; disable it instead")
        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            await self.repo.delete_app(app)
        except IntegrityError as exc:  # an assignment was added concurrently
            raise ConflictError("App has role assignments; disable it instead") from exc
        self.repo.mark_apps_changed()


class PermissionService:
    """Every mutation locks the app row first and decides every guard after it (ADR-0003 §10)."""

    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)

    async def list_permissions(
        self, key: str, *, limit: int, offset: int
    ) -> tuple[list[Permission], int]:
        app = await self.repo.get_app_by_key(key)
        if app is None:
            raise NotFoundError("App not found")
        return await self.repo.page_permissions(app.id, limit=limit, offset=offset)

    async def _get(self, app: App, permission_id: uuid.UUID) -> Permission:
        perm = await self.repo.get_permission(app.id, permission_id)
        if perm is None:
            raise NotFoundError("Permission not found")
        return perm

    async def create(
        self, key: str, data: PermissionCreate, actor: uuid.UUID | None = None
    ) -> Permission:
        app = await _locked_app(self.repo, key)
        if data.key.split(":", 1)[0] != app.key:
            raise _prefix_mismatch()
        if data.key == f"{app.key}:*":
            if data.is_admin is False:
                raise _wildcard_must_be_admin()
            is_admin = True
        else:
            is_admin = bool(data.is_admin)
        # ADM-002 guard: creating an is_admin or <app>:* permission, or any admin-app
        # permission, requires admin:*.
        if await self.repo.get_permission_by_key(app.id, data.key) is not None:
            raise ConflictError("Permission key is already taken")
        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            return await self.repo.add_permission(
                Permission(
                    app_id=app.id, key=data.key, description=data.description, is_admin=is_admin
                )
            )
        except IntegrityError as exc:  # concurrent insert with the same key
            raise ConflictError("Permission key is already taken") from exc

    async def update(
        self,
        key: str,
        permission_id: uuid.UUID,
        data: PermissionUpdate,
        actor: uuid.UUID | None = None,
    ) -> Permission:
        app = await _locked_app(self.repo, key)
        perm = await self._get(app, permission_id)
        fields = data.model_fields_set

        if "is_admin" in fields and data.is_admin is False and perm.key.endswith(":*"):
            raise _wildcard_must_be_admin()
        if (
            "is_admin" in fields
            and data.is_admin is True
            and not perm.is_admin
            and app.default_role_id is not None
            and await self.repo.role_holds_permission(app.default_role_id, perm.id)
        ):
            raise ConflictError("The app's default role holds this permission")

        # ADM-002 guard: changing is_admin requires admin:* (true -> false makes a permission
        # eligible for the default role).
        changed = False
        for field in fields:
            value = getattr(data, field)
            if getattr(perm, field) != value:
                setattr(perm, field, value)
                changed = True
        if not changed:
            return perm

        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            await self.repo.save()
        except IntegrityError as exc:
            raise ConflictError("Permission was changed concurrently") from exc
        await self.repo.session.refresh(perm)
        return perm

    async def delete(
        self, key: str, permission_id: uuid.UUID, actor: uuid.UUID | None = None
    ) -> None:
        app = await _locked_app(self.repo, key)
        perm = await self._get(app, permission_id)
        if app.key == ADMIN_APP_KEY and perm.key == ADMIN_WILDCARD:
            raise ConflictError("The admin wildcard permission cannot be deleted")
        if await self.repo.permission_held_by_any_role(perm.id):
            raise ConflictError("Permission is held by a role; remove it from the roles first")
        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            await self.repo.delete_permission(perm)
        except IntegrityError as exc:
            raise ConflictError("Permission was changed concurrently") from exc


class RoleService:
    """Every mutation locks the app row first and decides every guard after it (ADR-0003 §10)."""

    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)

    async def _get(self, app: App, role_id: uuid.UUID) -> Role:
        role = await self.repo.get_role(app.id, role_id)
        if role is None:
            raise NotFoundError("Role not found")
        return role

    async def _read(self, role: Role) -> RoleRead:
        keys = await self.repo.role_permission_keys(role.id)
        return RoleRead(
            id=role.id,
            key=role.key,
            name=role.name,
            is_system=role.is_system,
            permissions=sorted(keys.values()),
        )

    async def create(self, key: str, data: RoleCreate, actor: uuid.UUID | None = None) -> RoleRead:
        app = await _locked_app(self.repo, key)
        if data.key.split(":", 1)[0] != app.key:
            raise _prefix_mismatch()
        # ADM-002 guard: creating an admin-app role requires admin:*.
        if await self.repo.get_role_by_key(app.id, data.key) is not None:
            raise ConflictError("Role key is already taken")
        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            role = await self.repo.add_role(
                Role(app_id=app.id, key=data.key, name=data.name, is_system=False)
            )
        except IntegrityError as exc:  # concurrent insert with the same key
            raise ConflictError("Role key is already taken") from exc
        return RoleRead(
            id=role.id, key=role.key, name=role.name, is_system=role.is_system, permissions=[]
        )

    async def update(
        self, key: str, role_id: uuid.UUID, data: RoleUpdate, actor: uuid.UUID | None = None
    ) -> RoleRead:
        app = await _locked_app(self.repo, key)
        role = await self._get(app, role_id)
        # ADM-002 guard: editing an admin-app role requires admin:*.
        if data.name is not None and data.name != role.name:
            role.name = data.name
            # R4: audit entry in the same transaction once ADM-004 lands.
            try:
                await self.repo.save()
            except IntegrityError as exc:
                raise ConflictError("Role was changed concurrently") from exc
            await self.repo.session.refresh(role)
        return await self._read(role)

    async def delete(self, key: str, role_id: uuid.UUID, actor: uuid.UUID | None = None) -> None:
        app = await _locked_app(self.repo, key)
        role = await self._get(app, role_id)
        if role.is_system:
            raise ConflictError("System roles cannot be deleted")
        if role.id == app.default_role_id:
            raise ConflictError("The app's default role cannot be deleted")
        if await self.repo.role_has_assignments(role.id):
            raise ConflictError("Role has assignments")
        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            await self.repo.delete_role(role)
        except IntegrityError as exc:  # an assignment or default was added concurrently
            raise ConflictError("Role was changed concurrently") from exc

    async def replace_permissions(
        self,
        key: str,
        role_id: uuid.UUID,
        data: RolePermissionsReplace,
        actor: uuid.UUID | None = None,
    ) -> RoleRead:
        app = await _locked_app(self.repo, key)
        role = await self._get(app, role_id)
        keys = list(dict.fromkeys(data.permission_keys))
        found = {p.key: p for p in await self.repo.permissions_by_keys(app.id, keys)}
        missing = sorted(set(keys) - found.keys())
        if missing:
            raise UnprocessableError(
                "Unknown permissions for this app",
                details=[
                    {"field": "permission_keys", "reason": "unknown_permission", "key": k}
                    for k in missing
                ],
            )
        if role.id == app.default_role_id and any(p.is_admin for p in found.values()):
            raise ConflictError("A default role must not hold admin permissions")
        if (
            app.key == ADMIN_APP_KEY
            and role.key == SUPER_ADMIN_ROLE
            and ADMIN_WILDCARD not in found
        ):
            raise ConflictError("admin:super-admin must keep admin:*")
        # ADM-002 guard: granting <app>:* or any is_admin permission, and any admin-app role
        # edit, requires admin:*.
        # R4: audit entry in the same transaction once ADM-004 lands.
        await _set_role_permissions(self.repo, role, {p.id: p.key for p in found.values()})
        return RoleRead(
            id=role.id,
            key=role.key,
            name=role.name,
            is_system=role.is_system,
            permissions=sorted(found),
        )


def _assignment_unprocessable(message: str, field: str, reason: str) -> UnprocessableError:
    return UnprocessableError(message, details=[{"field": field, "reason": reason}])


def _assignment_read(row: AssignmentRow) -> AssignmentRead:
    ra, app_key, role_key, active = row
    return AssignmentRead(
        id=ra.id,
        app=app_key,
        role_id=ra.role_id,
        role_key=role_key,
        org_id=ra.org_id,
        granted_by=ra.granted_by,
        expires_at=ra.expires_at,
        active=active,
        created_at=ra.created_at,
    )


def _is_super_admin(app: App, role: Role) -> bool:
    return app.key == ADMIN_APP_KEY and role.key == SUPER_ADMIN_ROLE


class AssignmentService:
    """Writes lock the role's app first and decide every guard after it (ADR-0003 §10).

    Revoking an `admin:super-admin` row therefore holds the `admin` lock, which serializes the
    last-super-admin guard. The guard relies on READ COMMITTED (each statement sees commits made
    before it ran).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)

    async def list(
        self, user_id: uuid.UUID, *, app_key: str | None, limit: int, offset: int
    ) -> tuple[list[AssignmentRead], int]:
        if not await self.repo.user_exists(user_id):
            raise NotFoundError("User not found")
        rows, total = await self.repo.page_assignments(
            user_id, app_key=app_key, limit=limit, offset=offset
        )
        return [_assignment_read(r) for r in rows], total

    async def assign(
        self, user_id: uuid.UUID, data: AssignmentCreate, actor: uuid.UUID | None = None
    ) -> AssignmentRead:
        if not await self.repo.user_exists(user_id):
            raise NotFoundError("User not found")
        found = await self.repo.role_with_app(data.role_id)
        if found is None:
            raise _assignment_unprocessable("Unknown role", "role_id", "unknown_role")
        app = await _locked_app(self.repo, found[1].key)
        role = await self.repo.get_role(app.id, data.role_id)
        if role is None:  # deleted between the read and the lock
            raise _assignment_unprocessable("Unknown role", "role_id", "unknown_role")
        ra, _ = await self._grant_locked(app, role, user_id, data.org_id, data.expires_at, actor)
        row = await self.repo.get_assignment_row(user_id, ra.id)
        if row is None:  # unreachable while the transaction holds the app lock
            raise ConflictError("Assignment was changed concurrently")
        return _assignment_read(row)

    async def bootstrap_super_admin(self, email: str) -> BootstrapResult:
        """CLI only (USR-02f). The caller owns the transaction; `email` is already normalized.

        Lock order: admin app (`FOR NO KEY UPDATE`) -> user row (`FOR SHARE`), app -> rows as in
        ADR-0003 §10. A future disable/delete path that takes `lock_app("admin")` must lock in
        the same order. Creates no user and never marks an email verified (D16).
        """
        app = await self.repo.lock_app(ADMIN_APP_KEY)
        role = await self.repo.get_role_by_key(app.id, SUPER_ADMIN_ROLE) if app else None
        if app is None or role is None:
            return BootstrapResult(BootstrapOutcome.NOT_SEEDED)
        user = await UserRepository(self.repo.session).get_by_email_for_share(email)
        if user is None:
            return BootstrapResult(BootstrapOutcome.UNKNOWN_EMAIL)
        if user.status != UserStatus.ACTIVE.value:
            return BootstrapResult(BootstrapOutcome.USER_DISABLED)
        outcome = BootstrapOutcome.GRANTED
        existing = await self.repo.find_assignment(user.id, role.id, None)
        if existing is not None and (
            existing.expires_at is None or existing.expires_at > await self.repo.db_now()
        ):
            outcome = BootstrapOutcome.ALREADY_HELD
        else:
            # R4: ADM-004 audit entry (actor system:cli) in the same transaction.
            await self._grant_locked(app, role, user.id, None, None, None)
        return BootstrapResult(
            outcome,
            user_id=user.id,
            email=user.email,
            created_at=user.created_at,
            email_verified=user.email_verified_at is not None,
        )

    async def _grant_locked(
        self,
        app: App,
        role: Role,
        user_id: uuid.UUID,
        org_id: uuid.UUID | None,
        expires_at: datetime | None,
        actor: uuid.UUID | None,
    ) -> tuple[RoleAssignment, bool]:
        """The caller holds `lock_app(app.key)`. Returns (assignment, created)."""
        if org_id is not None:
            if app.org_mode == OrgMode.NONE:
                raise _assignment_unprocessable(
                    "This app does not use organizations", "org_id", "org_id_not_allowed"
                )
            if not await self.repo.org_exists_key_share(org_id):
                raise _assignment_unprocessable("Unknown organization", "org_id", "unknown_org")
        if expires_at is not None:
            if _is_super_admin(app, role):
                raise _assignment_unprocessable(
                    "admin:super-admin cannot expire", "expires_at", "expiry_not_allowed"
                )
            if expires_at <= await self.repo.db_now():
                raise _assignment_unprocessable(
                    "expires_at must be in the future", "expires_at", "expires_in_past"
                )
        # ADM-002 guard: granting an admin-app role, or a role holding any is_admin permission,
        # requires admin:*; an actor may not grant themselves any admin-app role; an actor may
        # not grant permissions they do not hold.

        existing = await self.repo.find_assignment(user_id, role.id, org_id)
        if existing is not None:
            if existing.expires_at is None or existing.expires_at > await self.repo.db_now():
                raise ConflictError("Role is already assigned")
            # Expired duplicate: replaced in place (same id and created_at).
            existing.expires_at = expires_at
            existing.granted_by = actor
            # R4: audit entry in the same transaction once ADM-004 lands.
            try:
                await self.repo.save()
            except IntegrityError as exc:
                raise ConflictError("Assignment was changed concurrently") from exc
            return existing, False

        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            ra = await self.repo.add_assignment(
                RoleAssignment(
                    user_id=user_id,
                    role_id=role.id,
                    org_id=org_id,
                    expires_at=expires_at,
                    granted_by=actor,
                )
            )
        except IntegrityError as exc:  # racing insert, or the user was deleted meanwhile
            raise ConflictError("Role is already assigned") from exc
        return ra, True

    async def revoke(
        self, user_id: uuid.UUID, assignment_id: uuid.UUID, actor: uuid.UUID | None = None
    ) -> None:
        row = await self.repo.get_assignment_row(user_id, assignment_id)
        if row is None:
            raise NotFoundError("Assignment not found")
        app = await _locked_app(self.repo, row[1])
        row = await self.repo.get_assignment_row(user_id, assignment_id)
        if row is None:  # revoked between the read and the lock
            raise NotFoundError("Assignment not found")
        ra, _, role_key, _ = row
        if (
            app.key == ADMIN_APP_KEY
            and role_key == SUPER_ADMIN_ROLE
            and await self.repo.is_counting_super_admin(ra.role_id, ra.id)
            and await self.repo.count_other_active_super_admins(ra.role_id, ra.id) == 0
        ):
            raise ConflictError("Cannot revoke the last active super-admin")
        # ADM-002 guard: revoking an admin-app role, or a role holding any is_admin permission,
        # requires admin:*; self-revoke still goes through the last-super-admin guard above.
        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            await self.repo.delete_assignment(ra)
        except IntegrityError as exc:
            raise ConflictError("Assignment was changed concurrently") from exc
