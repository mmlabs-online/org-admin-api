import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    DENIED_ERRORS,
    AppKeyTakenError,
    ConflictError,
    LastSuperAdminError,
    NotFoundError,
    PreconditionFailedError,
    RoleHasAssignmentsError,
    SelfAssignmentForbiddenError,
    UnprocessableError,
)
from app.core.etag import IfMatch
from app.db.base import bumped_updated_at
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.rbac.actor import SYSTEM_CLI, Actor, actor_user_id, require_super
from app.modules.rbac.authz import is_designated_super_admin_role
from app.modules.rbac.models import (
    ADMIN_APP_KEY,
    APP_KEY_RE,
    App,
    AppStatus,
    JoinPolicy,
    OrgMode,
    Permission,
    PermissionScope,
    Role,
    RoleAssignment,
)
from app.modules.rbac.origins import is_loopback_origin
from app.modules.rbac.repository import AssignmentRow, OrgMemberRow, RbacRepository, RoleHolderRow
from app.modules.rbac.schemas import (
    AdminOriginsSet,
    AppCreate,
    AppRegister,
    AppSort,
    AppUpdate,
    AssignmentCreate,
    AssignmentRead,
    OrgMemberRead,
    PermissionCreate,
    PermissionUpdate,
    RoleCatalog,
    RoleCatalogEntry,
    RoleCreate,
    RoleDefinitionRead,
    RoleHolderRead,
    RolePermissionsReplace,
    RoleRead,
    RoleUpdate,
)
from app.modules.rbac.scopes import default_scope, reserved_scope
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


class RegisterOutcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class RegisterResult:
    """Plain values for the CLI (USR-07), so it never touches the ORM."""

    outcome: RegisterOutcome
    key: str
    name: str
    join_policy: str
    org_mode: str
    origins: tuple[str, ...]
    origins_added: tuple[str, ...]
    origins_removed: tuple[str, ...]
    changes: tuple[tuple[str, str, str], ...]  # (field, old, new) for name / join_policy
    permissions_added: tuple[str, ...]
    org_permissions_added: tuple[str, ...]
    member_role: str | None  # None: the member role is missing (not rebuilt)
    member_permissions: tuple[str, ...]
    member_added: tuple[str, ...]
    member_removed: tuple[str, ...]


MAX_ADMIN_ORIGINS = 2
APEX_ORIGIN = "https://mmlabs.online"


class AdminOriginsOutcome(StrEnum):
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class AdminOriginsResult:
    """Plain values for the CLI (ADM-002c), so it never touches the ORM."""

    outcome: AdminOriginsOutcome
    key: str
    status: str
    join_policy: str
    org_mode: str
    origins: tuple[str, ...]
    origins_added: tuple[str, ...]
    origins_removed: tuple[str, ...]


def _register_refusal(
    error: type[ConflictError] | type[UnprocessableError], message: str, field: str, reason: str
) -> ConflictError | UnprocessableError:
    return error(message, details=[{"field": field, "reason": reason}])


def _origin_taken() -> ConflictError:
    return ConflictError(
        "An origin is already registered to another app",
        details=[{"field": "origins", "reason": "origin_taken"}],
    )


def _validate_admin_origins(origins: list[str], app_env: str) -> None:
    """Request-shape rules for the `admin` app's origins (ADM-002c), shared by CLI and API."""
    if APEX_ORIGIN in origins:
        raise _register_refusal(
            UnprocessableError,
            "The apex origin cannot be an admin origin",
            "origins",
            "apex_not_allowed",
        )
    if len(origins) > MAX_ADMIN_ORIGINS:
        raise _register_refusal(
            UnprocessableError, "Too many admin origins", "origins", "too_many_origins"
        )
    if app_env in {"staging", "production"} and any(is_loopback_origin(o) for o in origins):
        raise _register_refusal(
            UnprocessableError,
            "Loopback origins are not allowed here",
            "origins",
            "loopback_not_allowed",
        )


def _role_has_assignments(field: str) -> RoleHasAssignmentsError:
    return RoleHasAssignmentsError(
        "A role holding this permission has assignments; revoke them first",
        details=[{"field": field, "reason": "role_has_assignments"}],
    )


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
    A change bumps `roles.updated_at` (ETag, USR-24); callers must hold `lock_app`.
    """
    current = await repo.role_permission_keys(role.id)
    to_add = target.keys() - current.keys()
    to_remove = current.keys() - target.keys()
    if to_add or to_remove:
        try:
            await repo.apply_role_permission_diff(role.id, role.app_id, to_add, to_remove)
        except IntegrityError as exc:
            raise ConflictError("Role permissions changed concurrently") from exc
        await repo.touch_role(role)
    return sorted(target[p] for p in to_add), sorted(current[p] for p in to_remove)


def _prefix_mismatch() -> UnprocessableError:
    return UnprocessableError(
        "Key prefix must be the app key",
        details=[{"field": "key", "reason": "prefix_mismatch"}],
    )


def _violates_reserved(app_key: str, key: str, scope: PermissionScope) -> bool:
    reserved = reserved_scope(app_key, key)
    return reserved is not None and reserved != scope


def _reserved_scope() -> UnprocessableError:
    return UnprocessableError(
        "This key has a fixed scope",
        details=[{"field": "scope", "reason": "reserved_scope"}],
    )


def _wildcard_must_be_admin() -> UnprocessableError:
    return UnprocessableError(
        "The app wildcard permission must be is_admin",
        details=[{"field": "is_admin", "reason": "wildcard_must_be_admin"}],
    )


def _admin_app_permission_must_be_admin() -> UnprocessableError:
    return UnprocessableError(
        "Admin app permissions must be is_admin",
        details=[{"field": "is_admin", "reason": "admin_app_permission_must_be_admin"}],
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

    async def build_default_role(self, app_key: str) -> DefaultRoleResult:
        """Create or update `<app>:member` = every permission of the app with `is_admin = false`.

        For `org_mode = required` apps also only `scope = 'app'` permissions (ADR-0015).

        Runs only when called (seed, app creation, USR-05 manifest), never automatically on
        permission changes: that would silently widen or undo manual tightening (D9: role
        content is data). `<app>:*` is never included (the CHECK forces it to `is_admin`). Sets
        `apps.default_role_id` only when it is NULL. Idempotent. Refuses the `admin` app (409).
        """
        return await self._build_for_app(await _locked_app(self.repo, app_key))

    async def _build_for_app(
        self, app: App, *, set_default: bool = True, only: set[str] | None = None
    ) -> DefaultRoleResult:
        """The caller holds the app lock (`lock_app`) or owns the uncommitted new app row.

        With `set_default=False`, `apps.default_role_id` is left as it is, even when NULL.
        With `only`, the target is the eligible permissions that are also in `only` (manifest
        override: narrows, never widens).
        Internal: never audits (the public mutator that calls it records one row).
        """
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

        org_app = app.org_mode == OrgMode.REQUIRED.value
        if org_app and app.org_owner_role_id == role.id:  # a rebuild would strip the owner role
            raise ConflictError(
                "The member role is the app's org owner role",
                details=[{"field": "org_owner_role_id", "reason": "member_is_org_owner"}],
            )
        # Org apps: org-level permissions never reach the member role (ADR-0015).
        scope = PermissionScope.APP.value if org_app else None
        target = {
            p.id: p.key
            for p in await self.repo.list_permissions(app.id, is_admin=False, scope=scope)
        }
        if only is not None:
            target = {pid: k for pid, k in target.items() if k in only}
        added, removed = await _set_role_permissions(self.repo, role, target)

        if set_default:
            await self.repo.set_default_role_if_null(app.id, role.id)
            await self.repo.session.refresh(app)

        return DefaultRoleResult(role_key=key, created=created, added=added, removed=removed)


class AppService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)
        self.catalog = RoleCatalogService(session)
        self.audit = AuditLogger(session)

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
        return await self.repo.list_apps(
            status=status,
            join_policy=join_policy,
            org_mode=org_mode,
            q=q,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    async def member_counts(self, apps: Sequence[App]) -> dict[uuid.UUID, int]:
        return await self.repo.member_counts([a.id for a in apps])

    async def get(self, key: str) -> App:
        app = await self.repo.get_app_by_key(key)
        if app is None:
            raise NotFoundError("App not found")
        return app

    async def create(self, data: AppCreate, *, actor: Actor, ctx: AuditContext) -> App:
        """No `admin:*` needed: a new app has no admins yet."""
        app = await self._create(data)
        await self.audit.record(
            AuditAction.APP_CREATED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.APP, app.id),
            app_key=app.key,
            changes=diff.diff(None, diff.snapshot(app, diff.APP)),
        )
        return app

    async def _create(self, data: AppCreate) -> App:
        if await self.repo.get_app_by_key(data.key) is not None:
            raise AppKeyTakenError()
        claimed = [o for o in data.origins if not is_loopback_origin(o)]
        if await self.repo.app_keys_holding_origins(claimed, data.key):  # G6
            raise _origin_taken()
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
            raise AppKeyTakenError() from exc
        # Same transaction: a failure here rolls the app back with the request.
        await self.catalog._build_for_app(app)
        self.repo.mark_apps_changed()
        return app

    async def update(
        self, key: str, data: AppUpdate, *, actor: Actor, ctx: AuditContext, app_env: str
    ) -> App:
        try:
            app, before = await self._update(key, data, actor=actor, app_env=app_env)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.APP_UPDATED, exc=exc, actor=actor, ctx=ctx, app_key=key
            )
            raise
        changes = diff.diff(before, diff.snapshot(app, diff.APP))
        if changes:
            await self.audit.record(
                AuditAction.APP_UPDATED,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.APP, app.id),
                app_key=app.key,
                changes=changes,
            )
        return app

    async def _update(
        self, key: str, data: AppUpdate, *, actor: Actor, app_env: str
    ) -> tuple[App, dict[str, Any]]:
        """Returns the app and its allowlisted snapshot from before the change."""
        app = await _locked_app(self.repo, key)
        before = diff.snapshot(app, diff.APP)
        fields = data.model_fields_set
        changes: dict[str, object] = {}
        for field in fields:
            value = getattr(data, field)
            if isinstance(value, StrEnum):
                value = value.value
            if getattr(app, field) != value:
                changes[field] = value

        if app.key == ADMIN_APP_KEY and changes:  # G1; a no-op PATCH needs no admin:*
            require_super(actor)
        if app.key == ADMIN_APP_KEY and (
            ("join_policy" in fields and data.join_policy != JoinPolicy.CLOSED)
            or ("default_role_id" in fields and data.default_role_id is not None)
            or ("status" in fields and data.status != AppStatus.ACTIVE)
            or ("org_mode" in fields and data.org_mode != OrgMode.NONE)
        ):
            raise ConflictError(
                "The admin app must stay active, closed, without org mode and without a "
                "default role"
            )

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

        if "super_admin_role_id" in changes:  # set or clear needs admin:* (ADR-0030)
            require_super(actor)
            if data.super_admin_role_id is not None:
                await self._check_super_admin_role(app, data.super_admin_role_id, changes)

        owner_role_id = (
            data.org_owner_role_id if "org_owner_role_id" in fields else app.org_owner_role_id
        )
        if owner_role_id is not None:
            if "org_owner_role_id" in fields:
                if await self.repo.get_role(app.id, owner_role_id) is None:
                    raise UnprocessableError(
                        "Unknown role for this app",
                        details=[{"field": "org_owner_role_id", "reason": "unknown_role"}],
                    )
                if await self.repo.role_has_admin_permission(owner_role_id):
                    raise UnprocessableError(
                        "An org owner role must not hold admin permissions",
                        details=[
                            {"field": "org_owner_role_id", "reason": "role_has_admin_permissions"}
                        ],
                    )
            if changes.get("org_mode", app.org_mode) != OrgMode.REQUIRED.value:
                raise UnprocessableError(
                    "An org owner role needs org_mode required",
                    details=[{"field": "org_owner_role_id", "reason": "org_mode_required"}],
                )

        default_role_id = (
            data.default_role_id if "default_role_id" in fields else app.default_role_id
        )
        if owner_role_id is not None and owner_role_id == default_role_id:
            raise UnprocessableError(
                "The org owner role must differ from the default role",
                details=[{"field": "org_owner_role_id", "reason": "same_as_default_role"}],
            )
        if (
            changes.get("org_mode", app.org_mode) == OrgMode.REQUIRED.value
            and default_role_id is not None
            and ("default_role_id" in changes or "org_mode" in changes)
            and await self.repo.role_has_org_permission(default_role_id)
        ):
            raise UnprocessableError(
                "A default role of an org app must not hold org-level permissions",
                details=[{"field": "default_role_id", "reason": "role_has_org_permissions"}],
            )

        super_admin_role_id = (
            data.super_admin_role_id if "super_admin_role_id" in fields else app.super_admin_role_id
        )
        if super_admin_role_id is not None and super_admin_role_id in (
            changes.get("default_role_id"),
            changes.get("org_owner_role_id"),
        ):
            field = (
                "default_role_id"
                if changes.get("default_role_id") == super_admin_role_id
                else "org_owner_role_id"
            )
            raise UnprocessableError(
                "The default and org owner roles must differ from the super-admin role",
                details=[{"field": field, "reason": "same_as_super_admin_role"}],
            )
        if changes.get("org_mode") == OrgMode.NONE.value and super_admin_role_id is not None:
            raise ConflictError(
                "App has a super-admin role; clear the designation before switching org_mode "
                "to none",
                details=[{"field": "org_mode", "reason": "super_admin_role_designated"}],
            )
        if (
            changes.get("org_mode") == OrgMode.NONE.value
            and app.org_mode == OrgMode.REQUIRED.value
            and (
                await self.repo.app_has_entitlements(app.id)
                or await self.repo.app_has_org_assignments(app.id)
            )
        ):
            raise ConflictError(
                "App has organization data (entitlements or org role assignments); "
                "revoke them before switching org_mode to none",
                details=[{"field": "org_mode", "reason": "org_mode_in_use"}],
            )

        if "origins" in changes:
            origins = list(data.origins or [])
            if app.key == ADMIN_APP_KEY:
                _validate_admin_origins(origins, app_env)
            claimed = [o for o in origins if not is_loopback_origin(o)]
            if await self.repo.app_keys_holding_origins(claimed, app.key):  # G6
                raise _origin_taken()

        if not changes:
            return app, before  # no write: updated_at stays unchanged
        for field, value in changes.items():
            setattr(app, field, value)

        try:
            await self.repo.save()
        except IntegrityError as exc:  # e.g. the role was deleted concurrently
            raise ConflictError("App was changed concurrently") from exc
        self.repo.mark_apps_changed()
        await self.repo.session.refresh(app)
        return app, before

    async def _check_super_admin_role(
        self, app: App, role_id: uuid.UUID, changes: dict[str, object]
    ) -> None:
        """Designation preconditions (ADR-0030 §1); 422 for bad input, 409 for held roles."""

        def refuse(message: str, reason: str) -> UnprocessableError:
            return UnprocessableError(
                message, details=[{"field": "super_admin_role_id", "reason": reason}]
            )

        if app.key == ADMIN_APP_KEY:
            raise refuse("The admin app has no super-admin role", "admin_app_not_allowed")
        role = await self.repo.get_role(app.id, role_id)
        if role is None:
            raise refuse("Unknown role for this app", "unknown_role")
        if changes.get("org_mode", app.org_mode) != OrgMode.REQUIRED.value:
            raise refuse("A super-admin role needs org_mode required", "org_mode_required")
        if role.is_system:
            raise refuse("A system role cannot be the super-admin role", "system_role")
        default_role_id = changes.get("default_role_id", app.default_role_id)
        if role_id == default_role_id:
            raise refuse(
                "The super-admin role must differ from the default role", "same_as_default_role"
            )
        owner_role_id = changes.get("org_owner_role_id", app.org_owner_role_id)
        if role_id == owner_role_id:
            raise refuse(
                "The super-admin role must differ from the org owner role",
                "same_as_org_owner_role",
            )
        if await self.repo.role_has_org_permission(role_id):
            raise refuse(
                "The super-admin role must not hold org-level permissions",
                "role_has_org_permissions",
            )
        wildcard = await self.repo.permissions_by_keys(app.id, [f"{app.key}:*"])
        if any([await self.repo.role_holds_permission(role_id, p.id) for p in wildcard]):
            raise refuse(
                "The super-admin role must not hold the app wildcard", "wildcard_not_allowed"
            )
        if await self.repo.role_has_assignments(role_id):
            raise ConflictError(
                "The role has assignments; revoke them before designating it",
                details=[{"field": "super_admin_role_id", "reason": "role_has_assignments"}],
            )

    async def delete(self, key: str, *, actor: Actor, ctx: AuditContext) -> None:
        app = await _locked_app(self.repo, key)
        if app.key == ADMIN_APP_KEY:
            raise ConflictError("The admin app cannot be deleted")
        if await self.repo.app_has_assignments(app.id):
            raise ConflictError("App has role assignments; disable it instead")
        if await self.repo.app_has_entitlements(app.id):
            raise ConflictError(
                "App has entitlements; disable it instead",
                details=[{"field": "key", "reason": "app_has_entitlements"}],
            )
        app_id, before = app.id, diff.snapshot(app, diff.APP)
        try:
            await self.repo.delete_app(app)
        except IntegrityError as exc:  # an assignment was added concurrently
            raise ConflictError("App has role assignments; disable it instead") from exc
        self.repo.mark_apps_changed()
        await self.audit.record(
            AuditAction.APP_DELETED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.APP, app_id),
            app_key=key,
            changes=diff.diff(before, None),
        )

    async def register(self, data: AppRegister, *, app_env: str) -> RegisterResult:
        """Create or update an app from the operator CLI (USR-07); the caller owns the transaction.

        Origins replace the stored set; `join_policy`/`org_mode` apply only when given (an
        existing app's `org_mode` never changes); permissions are only added (`is_admin=false`)
        and then rebuild `<key>:member` (D9: a plain re-run keeps manual tightening). Never
        touches `status`, nor an existing app's `default_role_id` (a NULL default stays NULL;
        only app creation sets `<key>:member`). Every refusal happens before the first write.
        """
        if data.key == ADMIN_APP_KEY:
            raise _register_refusal(
                ConflictError, "The admin app cannot be registered", "key", "admin_app"
            )
        if app_env in {"staging", "production"} and any(
            is_loopback_origin(o) for o in data.origins
        ):
            raise _register_refusal(
                UnprocessableError,
                "Loopback origins are not allowed here",
                "origins",
                "loopback_not_allowed",
            )
        requested = list(dict.fromkeys(data.permissions))
        org_requested = list(dict.fromkeys(data.org_permissions))
        for field, keys in (("permissions", requested), ("org_permissions", org_requested)):
            for perm_key in keys:
                if perm_key.split(":", 1)[0] != data.key:
                    raise _register_refusal(
                        UnprocessableError,
                        "Key prefix must be the app key",
                        field,
                        "prefix_mismatch",
                    )
                if perm_key == f"{data.key}:*":
                    raise _register_refusal(
                        UnprocessableError,
                        "The app wildcard permission cannot be registered",
                        field,
                        "wildcard_not_allowed",
                    )
        if set(requested) & set(org_requested):
            raise _register_refusal(
                UnprocessableError,
                "A permission cannot be both app and org scope",
                "org_permissions",
                "scope_conflict",
            )
        for field, keys, scope in (
            ("org_permissions", org_requested, PermissionScope.ORG),
            ("permissions", requested, PermissionScope.APP),
        ):
            if any(_violates_reserved(data.key, k, scope) for k in keys):
                raise _register_refusal(
                    UnprocessableError, "This key has a fixed scope", field, "reserved_scope"
                )

        app = await self.repo.lock_app(data.key)
        existing: set[str] = set()
        org_existing: set[str] = set()
        if app is not None:
            if app.status == AppStatus.DISABLED:
                raise _register_refusal(ConflictError, "The app is disabled", "key", "app_disabled")
            if data.org_mode is not None and data.org_mode != app.org_mode:
                raise _register_refusal(
                    ConflictError,
                    "org_mode of an existing app cannot be changed",
                    "org_mode",
                    "org_mode_change",
                )
        resulting_mode = app.org_mode if app is not None else (data.org_mode or OrgMode.NONE).value
        org_app = resulting_mode == OrgMode.REQUIRED.value
        if org_requested and not org_app:
            raise _register_refusal(
                UnprocessableError,
                "org_permissions need org_mode required",
                "org_permissions",
                "org_mode_required",
            )
        if app is not None:
            perms = await self.repo.permissions_by_keys(app.id, [*requested, *org_requested])
            if any(p.is_admin for p in perms):
                raise _register_refusal(
                    ConflictError,
                    "An is_admin permission cannot be registered",
                    "permissions",
                    "is_admin_permission",
                )
            if org_app and any(
                (p.key in org_requested) != (p.scope == PermissionScope.ORG.value) for p in perms
            ):
                raise _register_refusal(
                    ConflictError,
                    "The stored scope differs from the requested one",
                    "permissions",
                    "scope_mismatch",
                )
            existing = {p.key for p in perms if p.key in requested}
            org_existing = {p.key for p in perms if p.key in org_requested}

        claimed = [o for o in data.origins if not is_loopback_origin(o)]
        if await self.repo.app_keys_holding_origins(claimed, data.key):
            raise _origin_taken()

        changes: list[tuple[str, str, str]] = []
        created = app is None
        if app is None:
            app = await self._create(
                AppCreate(
                    key=data.key,
                    name=data.name,
                    join_policy=data.join_policy or JoinPolicy.OPEN,
                    org_mode=data.org_mode or OrgMode.NONE,
                    origins=data.origins,
                )
            )
            origins_added, origins_removed = sorted(app.origins), []
        else:
            old_name, old_policy, old_origins = app.name, app.join_policy, list(app.origins)
            fields: dict[str, object] = {"name": data.name}
            if set(data.origins) != set(old_origins):
                fields["origins"] = data.origins
            if data.join_policy is not None:
                fields["join_policy"] = data.join_policy
            app, _ = await self._update(
                data.key, AppUpdate.model_validate(fields), actor=SYSTEM_CLI, app_env=app_env
            )
            if app.name != old_name:
                changes.append(("name", old_name, app.name))
            if app.join_policy != old_policy:
                changes.append(("join_policy", old_policy, app.join_policy))
            origins_added = sorted(set(app.origins) - set(old_origins))
            origins_removed = sorted(set(old_origins) - set(app.origins))

        added = [k for k in requested if k not in existing]
        org_added = [k for k in org_requested if k not in org_existing]
        permission_service = PermissionService(self.repo.session)
        for scope, keys in ((PermissionScope.APP, added), (PermissionScope.ORG, org_added)):
            for perm_key in keys:
                await permission_service._create(
                    data.key,
                    PermissionCreate(key=perm_key, is_admin=False, scope=scope),
                    actor=SYSTEM_CLI,
                )

        member_added: list[str] = []
        member_removed: list[str] = []
        if added or org_added:
            # A new app got its default in `create()`; an existing app's default is staff data.
            built = await self.catalog._build_for_app(app, set_default=False)
            member_added, member_removed = built.added, built.removed

        member_key = f"{data.key}:member"
        role = await self.repo.get_role_by_key(app.id, member_key)
        member_permissions = (
            [] if role is None else sorted((await self.repo.role_permission_keys(role.id)).values())
        )

        if created:
            outcome = RegisterOutcome.CREATED
        elif (
            changes
            or origins_added
            or origins_removed
            or added
            or org_added
            or member_added
            or member_removed
        ):
            outcome = RegisterOutcome.UPDATED
        else:
            outcome = RegisterOutcome.UNCHANGED
        if outcome != RegisterOutcome.UNCHANGED:
            audit: dict[str, diff.Change] = {}
            if created:
                audit.update(diff.diff(None, diff.snapshot(app, diff.APP)))
            else:
                audit.update({field: diff.change(old, new) for field, old, new in changes})
                if origins_added or origins_removed:
                    audit["origins"] = diff.change(origins_removed, origins_added)
            if added:
                audit["permissions_added"] = diff.change([], sorted(added))
            if org_added:
                audit["org_permissions_added"] = diff.change([], sorted(org_added))
            if member_added or member_removed:
                audit["member_permissions"] = diff.change(member_removed, member_added)
            await self.audit.record(
                AuditAction.APP_REGISTERED,
                actor=SYSTEM_CLI,
                ctx=AuditContext.NONE,
                target=(AuditTarget.APP, app.id),
                app_key=app.key,
                changes=audit,
            )
        return RegisterResult(
            outcome=outcome,
            key=app.key,
            name=app.name,
            join_policy=app.join_policy,
            org_mode=app.org_mode,
            origins=tuple(app.origins),
            origins_added=tuple(origins_added),
            origins_removed=tuple(origins_removed),
            changes=tuple(changes),
            permissions_added=tuple(sorted(added)),
            org_permissions_added=tuple(sorted(org_added)),
            member_role=None if role is None else member_key,
            member_permissions=tuple(member_permissions),
            member_added=tuple(member_added),
            member_removed=tuple(member_removed),
        )

    async def set_admin_origins(self, data: AdminOriginsSet, *, app_env: str) -> AdminOriginsResult:
        """Replace the `admin` app's origins (ADM-002c); the caller owns the transaction.

        Writes only `origins`: never `status`, `join_policy`, `org_mode`, `default_role_id` or
        the admin roles. Fails closed (no repair) when the admin row is missing or has drifted
        from its seed, except that clearing (`origins == []`) skips the drift check: the
        break-glass must work on a drifted row (ADM-002d). Every refusal happens before the
        first write.
        """
        origins = data.origins
        _validate_admin_origins(origins, app_env)

        app = await self.repo.lock_app(ADMIN_APP_KEY)
        if app is None or (
            origins
            and (
                app.status != AppStatus.ACTIVE
                or app.join_policy != JoinPolicy.CLOSED
                or app.org_mode != OrgMode.NONE
                or app.default_role_id is not None
            )
        ):
            raise _register_refusal(
                ConflictError, "The admin app is not in its seeded state", "key", "admin_not_seeded"
            )

        claimed = [o for o in origins if not is_loopback_origin(o)]
        if await self.repo.app_keys_holding_origins(claimed, ADMIN_APP_KEY):
            raise _origin_taken()

        old = list(app.origins)
        outcome = AdminOriginsOutcome.UNCHANGED
        if set(origins) != set(old):
            app.origins = list(origins)
            try:
                await self.repo.save()
            except IntegrityError as exc:
                raise ConflictError("App was changed concurrently") from exc
            self.repo.mark_apps_changed()
            outcome = AdminOriginsOutcome.UPDATED
            await self.audit.record(
                AuditAction.APP_ADMIN_ORIGINS_SET,
                actor=SYSTEM_CLI,
                ctx=AuditContext.NONE,
                target=(AuditTarget.APP, app.id),
                app_key=app.key,
                changes={"origins": diff.change(old, app.origins)},
            )
        return AdminOriginsResult(
            outcome=outcome,
            key=app.key,
            status=app.status,
            join_policy=app.join_policy,
            org_mode=app.org_mode,
            origins=tuple(app.origins),
            origins_added=tuple(sorted(set(app.origins) - set(old))),
            origins_removed=tuple(sorted(set(old) - set(app.origins))),
        )


class PermissionService:
    """Every mutation locks the app row first and decides every guard after it (ADR-0003 §10)."""

    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)
        self.audit = AuditLogger(session)

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
        self, key: str, data: PermissionCreate, *, actor: Actor, ctx: AuditContext
    ) -> Permission:
        try:
            perm = await self._create(key, data, actor=actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.PERMISSION_CREATED, exc=exc, actor=actor, ctx=ctx, app_key=key
            )
            raise
        await self.audit.record(
            AuditAction.PERMISSION_CREATED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.PERMISSION, perm.id),
            app_key=key,
            changes=diff.diff(None, diff.snapshot(perm, diff.PERMISSION)),
        )
        return perm

    async def _create(
        self,
        key: str,
        data: PermissionCreate,
        *,
        actor: Actor,
        admin_create_allowed: bool = False,
    ) -> Permission:
        app = await _locked_app(self.repo, key)
        if data.key.split(":", 1)[0] != app.key:
            raise _prefix_mismatch()
        if data.key == f"{app.key}:*":
            if data.is_admin is False:
                raise _wildcard_must_be_admin()
            is_admin = True
        elif app.key == ADMIN_APP_KEY:  # admin-app permissions are always is_admin (ADM-002d)
            if data.is_admin is False:
                raise _admin_app_permission_must_be_admin()
            is_admin = True
        else:
            is_admin = bool(data.is_admin)
        # is_admin includes <app>:*; the manifest may create inert is_admin permissions of
        # other apps (ADR-0016 §6)
        if app.key == ADMIN_APP_KEY or (is_admin and not admin_create_allowed):
            require_super(actor)
        if data.scope is not None and _violates_reserved(app.key, data.key, data.scope):
            raise _reserved_scope()
        scope = data.scope or default_scope(app.key, app.org_mode, data.key)
        if (
            scope == PermissionScope.APP
            and app.org_mode == OrgMode.REQUIRED.value
            and reserved_scope(app.key, data.key) is None
        ):  # a new app-scope permission reaches the member role of an org app (ADR-0015)
            require_super(actor)
        if await self.repo.get_permission_by_key(app.id, data.key) is not None:
            raise ConflictError("Permission key is already taken")
        try:
            return await self.repo.add_permission(
                Permission(
                    app_id=app.id,
                    key=data.key,
                    description=data.description,
                    is_admin=is_admin,
                    scope=scope.value,
                )
            )
        except IntegrityError as exc:  # concurrent insert with the same key
            raise ConflictError("Permission key is already taken") from exc

    async def update(
        self,
        key: str,
        permission_id: uuid.UUID,
        data: PermissionUpdate,
        *,
        actor: Actor,
        ctx: AuditContext,
    ) -> Permission:
        app = await _locked_app(self.repo, key)
        perm = await self._get(app, permission_id)
        before = diff.snapshot(perm, diff.PERMISSION)
        fields = data.model_fields_set

        if "is_admin" in fields and data.is_admin is False and perm.key.endswith(":*"):
            raise _wildcard_must_be_admin()
        if "is_admin" in fields and data.is_admin is False and app.key == ADMIN_APP_KEY:
            raise _admin_app_permission_must_be_admin()
        if data.scope is not None and _violates_reserved(app.key, perm.key, data.scope):
            raise _reserved_scope()
        changes: dict[str, Any] = {
            f: getattr(data, f) for f in fields if getattr(perm, f) != getattr(data, f)
        }
        if "scope" in changes:
            changes["scope"] = PermissionScope(changes["scope"]).value
        org_app = app.org_mode == OrgMode.REQUIRED.value
        # true -> false makes a permission eligible for the default role; false -> true can
        # move it out of members' reach (both directions cross the is_admin boundary).
        try:
            if (
                (app.key == ADMIN_APP_KEY and changes)
                or "is_admin" in changes
                or (changes.get("scope") == PermissionScope.APP.value and org_app)
            ):  # org -> app makes it eligible for the member role of an org app
                require_super(actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.PERMISSION_UPDATED,
                exc=exc,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.PERMISSION, permission_id),
                app_key=key,
            )
            raise
        if (
            "is_admin" in fields
            and data.is_admin is True
            and not perm.is_admin
            and app.default_role_id is not None
            and await self.repo.role_holds_permission(app.default_role_id, perm.id)
        ):
            raise ConflictError("The app's default role holds this permission")
        if (
            "is_admin" in fields
            and data.is_admin is True
            and not perm.is_admin
            and app.org_owner_role_id is not None
            and await self.repo.role_holds_permission(app.org_owner_role_id, perm.id)
        ):
            raise ConflictError("The app's org owner role holds this permission")
        if (
            changes.get("scope") == PermissionScope.ORG.value
            and org_app
            and app.default_role_id is not None
            and await self.repo.role_holds_permission(app.default_role_id, perm.id)
        ):
            raise ConflictError("The app's default role holds this permission")
        if (
            changes.get("scope") == PermissionScope.ORG.value
            and app.super_admin_role_id is not None
            and await self.repo.role_holds_permission(app.super_admin_role_id, perm.id)
        ):
            raise ConflictError(
                "The app's super-admin role holds this permission",
                details=[{"field": "scope", "reason": "super_admin_role_holds_permission"}],
            )
        if changes.get("is_admin") is True and await self.repo.permission_held_by_assigned_role(
            perm.id
        ):  # G3 (USR-02d): members would silently gain an admin permission
            raise _role_has_assignments("is_admin")

        if not changes:
            return perm
        for field, value in changes.items():
            setattr(perm, field, value)

        try:
            await self.repo.save()
        except IntegrityError as exc:
            raise ConflictError("Permission was changed concurrently") from exc
        await self.repo.session.refresh(perm)
        await self.audit.record(
            AuditAction.PERMISSION_UPDATED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.PERMISSION, perm.id),
            app_key=key,
            changes=diff.diff(before, diff.snapshot(perm, diff.PERMISSION)),
        )
        return perm

    async def delete(
        self, key: str, permission_id: uuid.UUID, *, actor: Actor, ctx: AuditContext
    ) -> None:
        app = await _locked_app(self.repo, key)
        perm = await self._get(app, permission_id)
        try:
            if app.key == ADMIN_APP_KEY:
                require_super(actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.PERMISSION_DELETED,
                exc=exc,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.PERMISSION, permission_id),
                app_key=key,
            )
            raise
        if app.key == ADMIN_APP_KEY and perm.key == ADMIN_WILDCARD:
            raise ConflictError("The admin wildcard permission cannot be deleted")
        if await self.repo.permission_held_by_any_role(perm.id):
            raise ConflictError("Permission is held by a role; remove it from the roles first")
        before = diff.snapshot(perm, diff.PERMISSION)
        try:
            await self.repo.delete_permission(perm)
        except IntegrityError as exc:
            raise ConflictError("Permission was changed concurrently") from exc
        await self.audit.record(
            AuditAction.PERMISSION_DELETED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.PERMISSION, permission_id),
            app_key=key,
            changes=diff.diff(before, None),
        )


def role_is_privileged(app: App, role_id: uuid.UUID, holds_admin_permission: bool) -> bool:
    """Mirrors `AssignmentService._guard_privileged` (ADR-0008 G4) and the repository's
    `user_has_privileged_assignment` (G5); change all three together (ADR-0030)."""
    return (
        app.key == ADMIN_APP_KEY
        or holds_admin_permission
        or is_designated_super_admin_role(app, role_id)
    )


class RoleService:
    """Every mutation locks the app row first and decides every guard after it (ADR-0003 §10)."""

    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)
        self.audit = AuditLogger(session)

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
            updated_at=role.updated_at,
        )

    async def _definitions(self, app: App, roles: Sequence[Role]) -> list[RoleDefinitionRead]:
        keys: dict[uuid.UUID, list[str]] = {}
        admin: set[uuid.UUID] = set()
        for role_id, perm_key, is_admin in await self.repo.role_permission_rows(
            [r.id for r in roles]
        ):
            keys.setdefault(role_id, []).append(perm_key)
            if is_admin:
                admin.add(role_id)
        return [
            RoleDefinitionRead(
                id=r.id,
                app=app.key,
                key=r.key,
                name=r.name,
                is_system=r.is_system,
                is_default=r.id == app.default_role_id,
                is_super_admin=is_designated_super_admin_role(app, r.id),
                privileged=role_is_privileged(app, r.id, r.id in admin),
                permissions=sorted(keys.get(r.id, [])),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in roles
        ]

    async def _app(self, key: str) -> App:
        app = await self.repo.get_app_by_key(key)
        if app is None:
            raise NotFoundError("App not found")
        return app

    async def list_definitions(
        self, key: str, *, limit: int, offset: int
    ) -> tuple[list[RoleDefinitionRead], int]:
        app = await self._app(key)
        roles, total = await self.repo.page_roles(app.id, limit=limit, offset=offset)
        return await self._definitions(app, roles), total

    async def get_definition(self, key: str, role_id: uuid.UUID) -> RoleDefinitionRead:
        app = await self._app(key)
        role = await self._get(app, role_id)
        return (await self._definitions(app, [role]))[0]

    async def list_holders(
        self,
        key: str,
        role_id: uuid.UUID,
        *,
        active: bool | None,
        org_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[RoleHolderRead], int]:
        app = await self._app(key)
        role = await self._get(app, role_id)
        rows, total = await self.repo.page_role_holders(
            role.id, active=active, org_id=org_id, limit=limit, offset=offset
        )
        return [_role_holder_read(r) for r in rows], total

    async def create(
        self, key: str, data: RoleCreate, *, actor: Actor, ctx: AuditContext
    ) -> RoleRead:
        app = await _locked_app(self.repo, key)
        if data.key.split(":", 1)[0] != app.key:
            raise _prefix_mismatch()
        try:
            if app.key == ADMIN_APP_KEY:
                require_super(actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.ROLE_CREATED, exc=exc, actor=actor, ctx=ctx, app_key=key
            )
            raise
        if await self.repo.get_role_by_key(app.id, data.key) is not None:
            raise ConflictError("Role key is already taken")
        try:
            role = await self.repo.add_role(
                Role(app_id=app.id, key=data.key, name=data.name, is_system=False)
            )
        except IntegrityError as exc:  # concurrent insert with the same key
            raise ConflictError("Role key is already taken") from exc
        await self.audit.record(
            AuditAction.ROLE_CREATED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ROLE, role.id),
            app_key=key,
            changes=diff.diff(None, diff.snapshot(role, diff.ROLE)),
        )
        return RoleRead(
            id=role.id,
            key=role.key,
            name=role.name,
            is_system=role.is_system,
            permissions=[],
            updated_at=role.updated_at,
        )

    async def update(
        self, key: str, role_id: uuid.UUID, data: RoleUpdate, *, actor: Actor, ctx: AuditContext
    ) -> RoleRead:
        app = await _locked_app(self.repo, key)
        role = await self._get(app, role_id)
        if data.name is not None and data.name != role.name:
            try:
                if app.key == ADMIN_APP_KEY:
                    require_super(actor)
            except DENIED_ERRORS as exc:
                await self.audit.record_denied(
                    AuditAction.ROLE_UPDATED,
                    exc=exc,
                    actor=actor,
                    ctx=ctx,
                    target=(AuditTarget.ROLE, role_id),
                    app_key=key,
                    facts={"role_key": role.key},
                )
                raise
            old_name, role.name = role.name, data.name
            role.updated_at = bumped_updated_at(Role.updated_at)
            try:
                await self.repo.save()
            except IntegrityError as exc:
                raise ConflictError("Role was changed concurrently") from exc
            await self.repo.session.refresh(role)
            await self.audit.record(
                AuditAction.ROLE_UPDATED,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.ROLE, role.id),
                app_key=key,
                changes={"name": diff.change(old_name, role.name)},
            )
        return await self._read(role)

    async def delete(
        self, key: str, role_id: uuid.UUID, *, actor: Actor, ctx: AuditContext
    ) -> None:
        app = await _locked_app(self.repo, key)
        role = await self._get(app, role_id)
        try:
            if app.key == ADMIN_APP_KEY:
                require_super(actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.ROLE_DELETED,
                exc=exc,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.ROLE, role_id),
                app_key=key,
                facts={"role_key": role.key},
            )
            raise
        if role.is_system:
            raise ConflictError("System roles cannot be deleted")
        if role.id == app.default_role_id:
            raise ConflictError("The app's default role cannot be deleted")
        if role.id == app.org_owner_role_id:
            raise ConflictError("The app's org owner role cannot be deleted")
        if is_designated_super_admin_role(app, role.id):
            raise ConflictError(
                "The app's super-admin role cannot be deleted; clear the designation first",
                details=[{"field": "role_id", "reason": "super_admin_role_designated"}],
            )
        if await self.repo.role_has_assignments(role.id):
            raise ConflictError("Role has assignments")
        before = diff.snapshot(role, diff.ROLE)
        try:
            await self.repo.delete_role(role)
        except IntegrityError as exc:  # an assignment or default was added concurrently
            raise ConflictError("Role was changed concurrently") from exc
        await self.audit.record(
            AuditAction.ROLE_DELETED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ROLE, role_id),
            app_key=key,
            changes=diff.diff(before, None),
        )

    async def replace_permissions(
        self,
        key: str,
        role_id: uuid.UUID,
        data: RolePermissionsReplace,
        *,
        actor: Actor,
        ctx: AuditContext,
        precondition: IfMatch | None = None,
    ) -> RoleRead:
        app = await _locked_app(self.repo, key)
        role = await self._get(app, role_id)
        if (
            precondition is not None
            and not precondition.any
            and role.updated_at not in precondition.versions
        ):
            raise PreconditionFailedError(role.updated_at)
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
        if app.key == ADMIN_APP_KEY and ADMIN_WILDCARD in found and role.key != SUPER_ADMIN_ROLE:
            raise UnprocessableError(
                "Only admin:super-admin may hold admin:*",
                details=[{"field": "permission_keys", "reason": "wildcard_super_admin_only"}],
            )
        # Diff-based, so an unchanged re-PUT never needs admin:* and never refuses.
        current = await self.repo.role_permission_keys(role.id)
        target_ids = {p.id for p in found.values()}
        added = [p for p in found.values() if p.id not in current]
        removed = await self.repo.permissions_by_keys(
            app.id, [k for pid, k in current.items() if pid not in target_ids]
        )
        try:
            if (
                (app.key == ADMIN_APP_KEY and (added or removed))
                or (is_designated_super_admin_role(app, role.id) and (added or removed))
                or any(p.is_admin for p in [*added, *removed])
            ):
                require_super(actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.ROLE_PERMISSIONS_REPLACED,
                exc=exc,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.ROLE, role_id),
                app_key=key,
                facts={
                    "role_key": role.key,
                    "permission_keys_added": sorted(p.key for p in added),
                    "permission_keys_removed": sorted(p.key for p in removed),
                },
            )
            raise
        if role.id == app.default_role_id and any(p.is_admin for p in found.values()):
            raise ConflictError("A default role must not hold admin permissions")
        if role.id == app.org_owner_role_id and any(p.is_admin for p in found.values()):
            raise ConflictError("An org owner role must not hold admin permissions")
        if (
            role.id == app.default_role_id
            and app.org_mode == OrgMode.REQUIRED.value
            and any(p.scope == PermissionScope.ORG.value for p in found.values())
        ):
            raise ConflictError("A default role of an org app must not hold org-level permissions")
        if is_designated_super_admin_role(app, role.id):
            if any(p.scope == PermissionScope.ORG.value for p in found.values()):
                raise ConflictError(
                    "The super-admin role must not hold org-level permissions",
                    details=[{"field": "permission_keys", "reason": "role_has_org_permissions"}],
                )
            if f"{app.key}:*" in found:
                raise ConflictError(
                    "The super-admin role must not hold the app wildcard",
                    details=[{"field": "permission_keys", "reason": "wildcard_not_allowed"}],
                )
        if (
            app.key == ADMIN_APP_KEY
            and role.key == SUPER_ADMIN_ROLE
            and ADMIN_WILDCARD not in found
        ):
            raise ConflictError("admin:super-admin must keep admin:*")
        if any(p.is_admin for p in added) and await self.repo.role_has_assignments(role.id):
            raise _role_has_assignments("permission_keys")  # G3 (USR-02d)
        added_keys, removed_keys = await _set_role_permissions(
            self.repo, role, {p.id: p.key for p in found.values()}
        )
        if added_keys or removed_keys:
            await self.audit.record(
                AuditAction.ROLE_PERMISSIONS_REPLACED,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.ROLE, role.id),
                app_key=key,
                changes={"permissions": diff.change(removed_keys, added_keys)},
            )
        return RoleRead(
            id=role.id,
            key=role.key,
            name=role.name,
            is_system=role.is_system,
            permissions=sorted(found),
            updated_at=role.updated_at,
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


def _role_holder_read(row: RoleHolderRow) -> RoleHolderRead:
    ra, app_key, role_key, active, email, display_name, user_status = row
    return RoleHolderRead(
        **_assignment_read((ra, app_key, role_key, active)).model_dump(),
        user_id=ra.user_id,
        email=email,
        display_name=display_name,
        user_status=UserStatus(user_status),
    )


def _org_member_read(row: OrgMemberRow) -> OrgMemberRead:
    ra, app_key, role_key, active, role_name, email, display_name, user_status = row
    return OrgMemberRead(
        assignment_id=ra.id,
        user_id=ra.user_id,
        email=email,
        display_name=display_name,
        user_status=UserStatus(user_status),
        app=app_key,
        role_id=ra.role_id,
        role_key=role_key,
        role_name=role_name,
        granted_by=ra.granted_by,
        created_at=ra.created_at,
        expires_at=ra.expires_at,
        active=active,
    )


def _is_super_admin(app: App, role: Role) -> bool:
    return app.key == ADMIN_APP_KEY and role.key == SUPER_ADMIN_ROLE


def assignment_changes(
    ra: RoleAssignment, role_key: str, *, granted: bool
) -> dict[str, diff.Change]:
    """Grant: `{from: null, to: v}`; revoke: `{from: v, to: null}`. The user id, never the email."""
    values = {
        "user_id": ra.user_id,
        "role_key": role_key,
        "role_id": ra.role_id,
        "org_id": ra.org_id,
        "expires_at": ra.expires_at,
    }
    return diff.diff(None, values) if granted else diff.diff(values, None)


class AssignmentService:
    """Writes lock the role's app first and decide every guard after it (ADR-0003 §10).

    Revoking an `admin:super-admin` row therefore holds the `admin` lock, which serializes the
    last-super-admin guard. The guard relies on READ COMMITTED (each statement sees commits made
    before it ran).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)
        self.audit = AuditLogger(session)

    async def list(
        self, user_id: uuid.UUID, *, app_key: str | None, limit: int, offset: int
    ) -> tuple[list[AssignmentRead], int]:
        if not await self.repo.user_exists(user_id):
            raise NotFoundError("User not found")
        rows, total = await self.repo.page_assignments(
            user_id, app_key=app_key, limit=limit, offset=offset
        )
        return [_assignment_read(r) for r in rows], total

    async def list_org_members(
        self,
        org_id: uuid.UUID,
        *,
        app_key: str | None,
        active: bool | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[OrgMemberRead], int]:
        if not await self.repo.org_exists(org_id):
            raise NotFoundError(f"Organization {org_id} not found")
        rows, total = await self.repo.page_org_members(
            org_id, app_key=app_key, active=active, limit=limit, offset=offset
        )
        return [_org_member_read(r) for r in rows], total

    async def assign(
        self, user_id: uuid.UUID, data: AssignmentCreate, *, actor: Actor, ctx: AuditContext
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
        try:
            ra, _ = await self._grant_locked(
                app, role, user_id, data.org_id, data.expires_at, actor=actor
            )
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.ROLE_ASSIGNMENT_GRANTED,
                exc=exc,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.USER, user_id),
                app_key=app.key,
                org_id=data.org_id,
                facts={"role_key": role.key},
            )
            raise
        row = await self.repo.get_assignment_row(user_id, ra.id)
        if row is None:  # unreachable while the transaction holds the app lock
            raise ConflictError("Assignment was changed concurrently")
        await self._record_grant(ra, app, role, actor, ctx)
        return _assignment_read(row)

    async def _record_grant(
        self, ra: RoleAssignment, app: App, role: Role, actor: Actor, ctx: AuditContext
    ) -> None:
        await self.audit.record(
            AuditAction.ROLE_ASSIGNMENT_GRANTED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ROLE_ASSIGNMENT, ra.id),
            app_key=app.key,
            org_id=ra.org_id,
            changes=assignment_changes(ra, role.key, granted=True),
        )

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
            ra, _ = await self._grant_locked(app, role, user.id, None, None, actor=SYSTEM_CLI)
            await self._record_grant(ra, app, role, SYSTEM_CLI, AuditContext.NONE)
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
        *,
        actor: Actor,
    ) -> tuple[RoleAssignment, bool]:
        """The caller holds `lock_app(app.key)`. Returns (assignment, created). Never audits."""
        designated = is_designated_super_admin_role(app, role.id)
        if designated and org_id is not None:  # before the org lookup: app scope only
            raise _assignment_unprocessable(
                "The super-admin role is app-scope only", "org_id", "org_id_not_allowed"
            )
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
        await self._guard_privileged(app, role.id, user_id, actor)
        # Plain read under the target app's lock; never lock `admin` here (ADR-0030 §6).
        if designated and not await self.repo.is_active_staff_super_admin(user_id):
            raise _assignment_unprocessable(
                "Only an active staff super-admin can hold the super-admin role",
                "user_id",
                "recipient_not_staff_super_admin",
            )
        granted_by = actor_user_id(actor)

        existing = await self.repo.find_assignment(user_id, role.id, org_id)
        if existing is not None:
            if existing.expires_at is None or existing.expires_at > await self.repo.db_now():
                raise ConflictError("Role is already assigned")
            # Expired duplicate: replaced in place (same id and created_at).
            existing.expires_at = expires_at
            existing.granted_by = granted_by
            try:
                await self.repo.save()
            except IntegrityError as exc:
                raise ConflictError("Assignment was changed concurrently") from exc
            return existing, False

        try:
            ra = await self.repo.add_assignment(
                RoleAssignment(
                    user_id=user_id,
                    role_id=role.id,
                    org_id=org_id,
                    expires_at=expires_at,
                    granted_by=granted_by,
                )
            )
        except IntegrityError as exc:  # racing insert, or the user was deleted meanwhile
            raise ConflictError("Role is already assigned") from exc
        return ra, True

    async def _guard_privileged(
        self, app: App, role_id: uuid.UUID, user_id: uuid.UUID, actor: Actor
    ) -> None:
        """A privileged role (admin app, holding an `is_admin` permission, or the app's designated
        super-admin role) needs `admin:*`; nobody grants or revokes their own admin-app or
        designated role, super-admins included. Mirrors `role_is_privileged` (ADR-0030)."""
        designated = is_designated_super_admin_role(app, role_id)
        if (
            app.key != ADMIN_APP_KEY
            and not designated
            and not await self.repo.role_has_admin_permission(role_id)
        ):
            return
        require_super(actor)
        if app.key == ADMIN_APP_KEY and user_id == actor_user_id(actor):
            raise SelfAssignmentForbiddenError("Cannot change your own admin roles")
        if designated and user_id == actor_user_id(actor):
            raise SelfAssignmentForbiddenError("Cannot change your own super-admin roles")

    async def revoke(
        self, user_id: uuid.UUID, assignment_id: uuid.UUID, *, actor: Actor, ctx: AuditContext
    ) -> None:
        row = await self.repo.get_assignment_row(user_id, assignment_id)
        if row is None:
            raise NotFoundError("Assignment not found")
        app = await _locked_app(self.repo, row[1])
        row = await self.repo.get_assignment_row(user_id, assignment_id)
        if row is None:  # revoked between the read and the lock
            raise NotFoundError("Assignment not found")
        ra, _, role_key, _ = row
        try:
            await self._guard_privileged(app, ra.role_id, user_id, actor)
            if (
                app.key == ADMIN_APP_KEY
                and role_key == SUPER_ADMIN_ROLE
                and await self.repo.is_counting_super_admin(ra.role_id, ra.id)
                and await self.repo.count_other_active_super_admins(ra.role_id, ra.id) == 0
            ):
                raise LastSuperAdminError("Cannot revoke the last active super-admin")
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.ROLE_ASSIGNMENT_REVOKED,
                exc=exc,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.ROLE_ASSIGNMENT, assignment_id),
                app_key=app.key,
                org_id=ra.org_id,
                facts={"role_key": role_key},
            )
            raise
        changes = assignment_changes(ra, role_key, granted=False)
        org_id = ra.org_id
        try:
            await self.repo.delete_assignment(ra)
        except IntegrityError as exc:
            raise ConflictError("Assignment was changed concurrently") from exc
        await self.audit.record(
            AuditAction.ROLE_ASSIGNMENT_REVOKED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ROLE_ASSIGNMENT, assignment_id),
            app_key=app.key,
            org_id=org_id,
            changes=changes,
        )
