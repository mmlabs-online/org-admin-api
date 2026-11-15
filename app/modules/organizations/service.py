import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.organizations.models import Organization
from app.modules.organizations.repository import OrganizationRepository
from app.modules.organizations.schemas import OrganizationCreate, OrganizationUpdate
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

    async def list(self, *, limit: int, offset: int) -> tuple[list[Organization], int]:
        return await self.repo.list(limit=limit, offset=offset)

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
        self, org_id: uuid.UUID, data: OrganizationUpdate, *, actor: Actor, ctx: AuditContext
    ) -> Organization:
        org = await self.get(org_id)
        before = diff.snapshot(org, diff.ORG)
        for field, value in data.model_dump(exclude_unset=True).items():
            if field in {"name", "status"} and value is None:
                continue  # non-nullable columns: treat explicit null as "no change"
            setattr(org, field, value)
        await self.repo.session.flush()
        await self.repo.session.refresh(org)
        await self._record(AuditAction.ORGANIZATION_UPDATED, org, before, actor, ctx)
        return org

    async def delete(self, org_id: uuid.UUID, *, actor: Actor, ctx: AuditContext) -> None:
        org = await self.get(org_id)
        before = diff.snapshot(org, diff.ORG)
        await self.repo.delete(org)
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
