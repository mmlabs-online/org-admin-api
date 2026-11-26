"""App permission manifest sync (USR-05b, ADR-0001 §6, ADR-0016 §6). HTTP-free (R1).

An app pushes the permissions (and optional extra roles) it declares; this service stores them.
It reuses the RBAC writers (`PermissionService._create`, `RoleCatalogService._build_for_app`) so
every guard of ADR-0003 / ADR-0015 applies, and refuses everything before the first write.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.modules.app_credentials.errors import AppCredentialAppDisabledError
from app.modules.app_credentials.service import AuthenticatedApp
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.rbac.models import (
    ADMIN_APP_KEY,
    AppStatus,
    OrgMode,
    Permission,
    PermissionScope,
    Role,
)
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.schemas import (
    ManifestMemberResult,
    ManifestNotDeclared,
    ManifestPermissionsResult,
    ManifestPut,
    ManifestResult,
    ManifestRolesResult,
    PermissionCreate,
)
from app.modules.rbac.scopes import reserved_scope
from app.modules.rbac.service import (
    PermissionService,
    RoleCatalogService,
    _set_role_permissions,
    _violates_reserved,
)


def _detail(field: str, reason: str, key: str | None = None) -> dict[str, str]:
    detail = {"field": field, "reason": reason}
    if key is not None:  # only keys that already passed the format pattern
        detail["key"] = key
    return detail


class ManifestService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)
        self.catalog = RoleCatalogService(session)
        self.permissions = PermissionService(session)
        self.audit = AuditLogger(session)

    @staticmethod
    def _shape_errors(app_key: str, data: ManifestPut) -> list[dict[str, str]]:
        """Checks that need no database state."""
        errors: list[dict[str, str]] = []
        declared = {p.key: p for p in data.permissions}
        seen: set[str] = set()
        for p in data.permissions:
            if p.key.split(":", 1)[0] != app_key:
                errors.append(_detail("permissions", "prefix_mismatch"))
            elif p.key == f"{app_key}:*":
                errors.append(_detail("permissions", "wildcard_not_allowed", p.key))
            elif p.key in seen:
                errors.append(_detail("permissions", "duplicate_key", p.key))
            elif p.scope is not None and _violates_reserved(app_key, p.key, p.scope):
                errors.append(_detail("permissions", "reserved_scope", p.key))
            seen.add(p.key)

        def refs(field: str, keys: list[str], admin_reason: str) -> None:
            for k in dict.fromkeys(keys):
                if k.split(":", 1)[0] != app_key:
                    errors.append(_detail(field, "prefix_mismatch"))
                elif k not in declared:
                    errors.append(_detail(field, "undeclared_permission", k))
                elif declared[k].is_admin:
                    errors.append(_detail(field, admin_reason, k))

        role_keys: set[str] = set()
        for role in data.roles:
            if role.key.split(":", 1)[0] != app_key:
                errors.append(_detail("roles", "prefix_mismatch"))
            elif role.key == f"{app_key}:member":
                errors.append(_detail("roles", "reserved_role", role.key))
            elif role.key in role_keys:
                errors.append(_detail("roles", "duplicate_key", role.key))
            role_keys.add(role.key)
            refs("roles", role.permissions, "admin_permission_in_role")
        if data.member is not None:
            refs("member", data.member.permissions, "ineligible_member_permission")
        return errors

    async def sync(
        self, key: str, data: ManifestPut, *, caller: AuthenticatedApp, ctx: AuditContext
    ) -> ManifestResult:
        shape = self._shape_errors(key, data)
        if shape:
            raise UnprocessableError("The manifest is invalid", details=shape)

        app = await self.repo.lock_app(key)
        if app is None:
            raise NotFoundError("App not found")
        if app.key == ADMIN_APP_KEY:
            raise ConflictError(
                "The admin app has no manifest", details=[_detail("key", "admin_app")]
            )
        if app.status != AppStatus.ACTIVE.value:  # re-check under the lock
            raise AppCredentialAppDisabledError("The app is disabled")
        org_app = app.org_mode == OrgMode.REQUIRED.value

        # Mode-dependent checks (422).
        resolved: dict[str, PermissionScope] = {}
        errors: list[dict[str, str]] = []
        for p in data.permissions:
            scope = p.scope or reserved_scope(key, p.key)
            if scope is None:
                if org_app:
                    errors.append(_detail("permissions", "scope_required", p.key))
                    continue
                scope = PermissionScope.APP
            if scope == PermissionScope.ORG and not org_app:
                errors.append(_detail("permissions", "org_mode_required", p.key))
            resolved[p.key] = scope
        if data.member is not None and org_app:
            errors.extend(
                _detail("member", "ineligible_member_permission", k)
                for k in dict.fromkeys(data.member.permissions)
                if resolved.get(k) == PermissionScope.ORG
            )
        if errors:
            raise UnprocessableError("The manifest is invalid", details=errors)

        # Conflict checks (409), still before any write.
        stored: dict[str, Permission] = {
            p.key: p
            for p in await self.repo.permissions_by_keys(app.id, [p.key for p in data.permissions])
        }
        conflicts: list[dict[str, str]] = []
        for p in data.permissions:
            existing = stored.get(p.key)
            if existing is not None:
                if existing.is_admin != p.is_admin:
                    conflicts.append(_detail("permissions", "is_admin_mismatch", p.key))
                if existing.scope != resolved[p.key].value:
                    conflicts.append(_detail("permissions", "scope_mismatch", p.key))
            elif (
                org_app
                and resolved[p.key] == PermissionScope.APP
                and reserved_scope(key, p.key) is None
            ):
                conflicts.append(_detail("permissions", "requires_staff", p.key))
        for r in data.roles:
            role = await self.repo.get_role_by_key(app.id, r.key)
            if role is not None and (
                role.is_system
                or role.id in (app.default_role_id, app.org_owner_role_id, app.super_admin_role_id)
            ):
                conflicts.append(_detail("roles", "role_not_manifest_managed", r.key))
        new_keys = [p.key for p in data.permissions if p.key not in stored]
        rebuild = data.member is not None or bool(new_keys)
        member_role = await self.repo.get_role_by_key(app.id, f"{key}:member")
        if (
            rebuild
            and org_app
            and member_role is not None
            and app.org_owner_role_id == member_role.id
        ):
            conflicts.append(_detail("member", "member_is_org_owner"))
        if conflicts:
            raise ConflictError("The manifest conflicts with the stored catalog", details=conflicts)

        # Apply (one transaction).
        by_key: dict[str, Permission] = dict(stored)
        for p in data.permissions:
            if p.key in stored:
                continue
            by_key[p.key] = await self.permissions._create(
                key,
                PermissionCreate(
                    key=p.key,
                    description=p.description,
                    is_admin=p.is_admin,
                    scope=resolved[p.key],
                ),
                actor=caller.actor,
                admin_create_allowed=True,
            )
        described: list[str] = []
        for p in data.permissions:
            existing = stored.get(p.key)
            if (
                existing is not None
                and p.description is not None
                and existing.description != p.description
            ):
                existing.description = p.description
                described.append(p.key)
        if described:
            await self.repo.save()

        roles_added: list[str] = []
        roles_updated: list[str] = []
        for r in data.roles:
            role = await self.repo.get_role_by_key(app.id, r.key)
            existed = role is not None
            if role is None:
                role = await self.repo.add_role(
                    Role(app_id=app.id, key=r.key, name=r.name, is_system=False)
                )
                roles_added.append(r.key)
            target = await self.repo.role_permission_keys(role.id)
            target.update({by_key[k].id: k for k in r.permissions})
            gained, _ = await _set_role_permissions(self.repo, role, target)
            if existed and gained:
                roles_updated.append(r.key)

        member = ManifestMemberResult(role_key=f"{key}:member", rebuilt=False, added=[], removed=[])
        member_created = False
        if data.member is not None:
            built = await self.catalog._build_for_app(
                app, set_default=False, only=set(data.member.permissions)
            )
        elif new_keys:
            built = await self.catalog._build_for_app(app, set_default=False)
        else:
            built = None
        if built is not None:
            member_created = built.created
            member = ManifestMemberResult(
                role_key=built.role_key, rebuilt=True, added=built.added, removed=built.removed
            )

        declared_keys = set(by_key)
        missing = [
            p
            for p in await self.repo.list_permissions(app.id)
            if p.key not in declared_keys and p.key != f"{key}:*"
        ]
        held = await self.repo.roles_holding(app.id, [p.id for p in missing])
        not_declared = [
            ManifestNotDeclared(key=p.key, held_by_roles=held.get(p.id, [])) for p in missing
        ]

        added = sorted(new_keys)
        described.sort()
        roles_added.sort()
        roles_updated.sort()
        changed = bool(
            added
            or described
            or roles_added
            or roles_updated
            or member_created
            or member.added
            or member.removed
        )
        if changed:
            changes: dict[str, Any] = {}
            for name, keys in (
                ("permissions_added", added),
                ("descriptions_updated", described),
                ("roles_added", roles_added),
                ("roles_updated", roles_updated),
            ):
                if keys:
                    changes[name] = diff.change([], keys)
            if member.added or member.removed:
                changes["member_permissions"] = diff.change(member.removed, member.added)
            await self.audit.record(
                AuditAction.APP_MANIFEST_SYNCED,
                actor=caller.actor,
                ctx=ctx,
                target=(AuditTarget.APP, app.id),
                app_key=key,
                changes=changes,
            )
        return ManifestResult(
            app=key,
            changed=changed,
            permissions=ManifestPermissionsResult(
                added=added, description_updated=described, not_in_manifest=not_declared
            ),
            roles=ManifestRolesResult(added=roles_added, updated=roles_updated),
            member=member,
        )
