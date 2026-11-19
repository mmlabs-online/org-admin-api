import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    NotFoundError,
    OrgHasAssignmentsError,
    OrgHasEntitlementsError,
    PreconditionFailedError,
)
from app.core.etag import IfMatch
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.organizations.models import Organization, OrganizationStatus
from app.modules.organizations.repository import OrganizationRepository
from app.modules.organizations.schemas import (
    OrganizationCreate,
    OrganizationSort,
    OrganizationUpdate,
)
from app.modules.rbac.actor import Actor


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = OrganizationRepository(session)
        self.audit = AuditLogger(session)

    async def get(self, org_id: uuid.UUID) -> Organization:
        org = await self.repo.get(org_id)
        if org is None:
            raise NotFoundError(f"Organization {org_id} not found")
        return org

    async def list(
        self,
        *,
        status: OrganizationStatus | None,
        q: str | None,
        sort: OrganizationSort,
        limit: int,
        offset: int,
    ) -> tuple[list[Organization], int]:
        return await self.repo.list(status=status, q=q, sort=sort, limit=limit, offset=offset)

    async def create(
        self, data: OrganizationCreate, *, actor: Actor, ctx: AuditContext
    ) -> Organization:
        if await self.repo.get_by_slug(data.slug):
            raise ConflictError(f"Slug '{data.slug}' is already taken")
        try:
            org = await self.repo.add(Organization(**data.model_dump()))
        except IntegrityError as exc:  # concurrent insert with the same slug
            raise ConflictError(f"Slug '{data.slug}' is already taken") from exc
        await self._record(AuditAction.ORGANIZATION_CREATED, org, None, actor, ctx)
        return org

    async def update(
        self,
        org_id: uuid.UUID,
        data: OrganizationUpdate,
        *,
        actor: Actor,
        ctx: AuditContext,
        precondition: IfMatch | None = None,
    ) -> Organization:
        org = await self.get(org_id)
        before = diff.snapshot(org, diff.ORG)
        fields = {
            field: value
            for field, value in data.model_dump(exclude_unset=True).items()
            if not (field in {"name", "status"} and value is None)
            # non-nullable columns: treat explicit null as "no change"
        }
        if precondition is not None and not precondition.any:
            if org.updated_at not in precondition.versions:
                raise PreconditionFailedError(org.updated_at)
            values = {f: v for f, v in fields.items() if getattr(org, f) != v}
            if not values:
                return org
            if not await self.repo.update_if_version(org_id, org.updated_at, values):
                current = await self.repo.current_version(org_id)
                if current is None:
                    raise NotFoundError(f"Organization {org_id} not found")
                raise PreconditionFailedError(current)
            await self.repo.session.refresh(org)
            await self._record(AuditAction.ORGANIZATION_UPDATED, org, before, actor, ctx)
            return org
        for field, value in fields.items():
            setattr(org, field, value)
        await self.repo.session.flush()
        await self.repo.session.refresh(org)
        await self._record(AuditAction.ORGANIZATION_UPDATED, org, before, actor, ctx)
        return org

    async def delete(self, org_id: uuid.UUID, *, actor: Actor, ctx: AuditContext) -> None:
        org = await self.get(org_id)
        before = diff.snapshot(org, diff.ORG)
        if await self.repo.has_role_assignments(org_id):
            raise OrgHasAssignmentsError(
                "Organization has role assignments; revoke them first",
                details=[{"field": "org_id", "reason": "org_has_assignments"}],
            )
        if await self.repo.has_entitlements(org_id):
            raise OrgHasEntitlementsError(
                "Organization has entitlements; revoke them first",
                details=[{"field": "org_id", "reason": "org_has_entitlements"}],
            )
        try:
            await self.repo.delete(org)
        except IntegrityError as exc:  # an assignment was added concurrently
            raise OrgHasAssignmentsError(
                "Organization has role assignments; revoke them first",
                details=[{"field": "org_id", "reason": "org_has_assignments"}],
            ) from exc
        await self.audit.record(
            AuditAction.ORGANIZATION_DELETED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ORGANIZATION, org_id),
            org_id=org_id,
            changes=diff.diff(before, None),
        )

    async def _record(
        self,
        action: AuditAction,
        org: Organization,
        before: dict[str, object] | None,
        actor: Actor,
        ctx: AuditContext,
    ) -> None:
        """Created/updated: no row when an update changed nothing."""
        changes = diff.diff(before, diff.snapshot(org, diff.ORG))
        if not changes:
            return
        await self.audit.record(
            action,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ORGANIZATION, org.id),
            org_id=org.id,
            changes=changes,
        )
