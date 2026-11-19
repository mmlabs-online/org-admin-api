"""Staff management of org entitlements (ADM-006). HTTP-free (R1).

Suspended org, disabled app or changed `org_mode` do not touch existing rows: writes stay
allowed and `active` reports the effective state. Only a new grant is refused for an app that
cannot carry it. A grant locks the app row so it serialises with `PATCH org_mode` (ADM-019).
"""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PreconditionFailedError
from app.core.etag import IfMatch
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.entitlements.models import OrgEntitlement
from app.modules.entitlements.repository import EntitlementRepository, EntitlementView
from app.modules.entitlements.schemas import EntitlementCreate, EntitlementUpdate
from app.modules.organizations.repository import OrganizationRepository
from app.modules.rbac.actor import Actor
from app.modules.rbac.models import ADMIN_APP_KEY, AppStatus, OrgMode
from app.modules.rbac.repository import RbacRepository


def _refusal(message: str, reason: str) -> ConflictError:
    return ConflictError(message, details=[{"field": "app_key", "reason": reason}])


class EntitlementService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = EntitlementRepository(session)
        self.orgs = OrganizationRepository(session)
        self.rbac = RbacRepository(session)
        self.audit = AuditLogger(session)

    async def _require_org(self, org_id: uuid.UUID) -> None:
        if await self.orgs.get(org_id) is None:
            raise NotFoundError(f"Organization {org_id} not found")

    async def _get(self, org_id: uuid.UUID, ent_id: uuid.UUID) -> OrgEntitlement:
        await self._require_org(org_id)
        ent = await self.repo.get_for_org(org_id, ent_id)
        if ent is None:
            raise NotFoundError("Entitlement not found")
        return ent

    async def _view(self, org_id: uuid.UUID, ent_id: uuid.UUID) -> EntitlementView:
        view = await self.repo.get_view(org_id, ent_id)
        assert view is not None  # noqa: S101 (just written in this transaction)
        return view

    async def get(self, org_id: uuid.UUID, ent_id: uuid.UUID) -> EntitlementView:
        await self._require_org(org_id)
        view = await self.repo.get_view(org_id, ent_id)
        if view is None:
            raise NotFoundError("Entitlement not found")
        return view

    async def list(
        self, org_id: uuid.UUID, *, active: bool | None, limit: int, offset: int
    ) -> tuple[list[EntitlementView], int]:
        await self._require_org(org_id)
        return await self.repo.page(org_id, active=active, limit=limit, offset=offset)

    async def grant(
        self, org_id: uuid.UUID, data: EntitlementCreate, *, actor: Actor, ctx: AuditContext
    ) -> EntitlementView:
        await self._require_org(org_id)
        if data.app_key == ADMIN_APP_KEY:  # before locking: keeps the admin lock ordering
            raise _refusal("The admin app has no entitlements", "admin_app")
        app = await self.rbac.lock_app(data.app_key)
        if app is None:
            raise NotFoundError(
                "App not found", details=[{"field": "app_key", "reason": "unknown_app"}]
            )
        if app.status == AppStatus.DISABLED.value:
            raise _refusal("The app is disabled", "app_disabled")
        if app.org_mode != OrgMode.REQUIRED.value:
            raise _refusal("The app does not use organizations", "org_mode_none")
        exists = _refusal("The organization already has this entitlement", "entitlement_exists")
        if await self.repo.find(org_id, app.id) is not None:
            raise exists
        ent = OrgEntitlement(
            org_id=org_id,
            app_id=app.id,
            plan=data.plan,
            limits=data.limits,
            enabled=data.enabled,
        )
        try:
            async with self.repo.session.begin_nested():
                await self.repo.add(ent)
        except IntegrityError as exc:  # concurrent grant for the same (org, app)
            raise exists from exc
        await self.audit.record(
            AuditAction.ENTITLEMENT_GRANTED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ENTITLEMENT, ent.id),
            app_key=app.key,
            org_id=org_id,
            changes=diff.diff(None, diff.snapshot(ent, diff.ENTITLEMENT)),
        )
        return await self._view(org_id, ent.id)

    async def update(
        self,
        org_id: uuid.UUID,
        ent_id: uuid.UUID,
        data: EntitlementUpdate,
        *,
        actor: Actor,
        ctx: AuditContext,
        precondition: IfMatch | None = None,
    ) -> EntitlementView:
        ent = await self._get(org_id, ent_id)
        before = diff.snapshot(ent, diff.ENTITLEMENT)
        fields = {
            field: value
            for field, value in data.model_dump(exclude_unset=True).items()
            if value is not None  # explicit null: no change
        }
        if precondition is not None and not precondition.any:
            if ent.updated_at not in precondition.versions:
                raise PreconditionFailedError(ent.updated_at)
            values = {f: v for f, v in fields.items() if getattr(ent, f) != v}
            if not values:
                return await self._view(org_id, ent_id)
            if not await self.repo.update_if_version(ent_id, ent.updated_at, values):
                current = await self.repo.current_version(org_id, ent_id)
                if current is None:
                    raise NotFoundError("Entitlement not found")
                raise PreconditionFailedError(current)
            await self.repo.session.refresh(ent)
        else:
            for field, value in fields.items():
                setattr(ent, field, value)  # a new dict for `limits` (no MutableDict)
            await self.repo.session.flush()
            await self.repo.session.refresh(ent)
        changes = diff.diff(before, diff.snapshot(ent, diff.ENTITLEMENT))
        if changes:
            await self.audit.record(
                AuditAction.ENTITLEMENT_UPDATED,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.ENTITLEMENT, ent.id),
                app_key=await self._app_key(ent),
                org_id=org_id,
                changes=changes,
            )
        return await self._view(org_id, ent.id)

    async def revoke(
        self, org_id: uuid.UUID, ent_id: uuid.UUID, *, actor: Actor, ctx: AuditContext
    ) -> None:
        ent = await self._get(org_id, ent_id)
        before = diff.snapshot(ent, diff.ENTITLEMENT)
        app_key = await self._app_key(ent)
        await self.repo.delete(ent)
        await self.audit.record(
            AuditAction.ENTITLEMENT_REVOKED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ENTITLEMENT, ent_id),
            app_key=app_key,
            org_id=org_id,
            changes=diff.diff(before, None),
        )

    async def _app_key(self, ent: OrgEntitlement) -> str:
        view = await self.repo.get_view(ent.org_id, ent.id)
        assert view is not None  # noqa: S101 (row loaded in this transaction)
        return view.app_key
