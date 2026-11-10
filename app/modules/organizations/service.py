import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.organizations.models import Organization
from app.modules.organizations.repository import OrganizationRepository
from app.modules.organizations.schemas import OrganizationCreate, OrganizationUpdate


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = OrganizationRepository(session)

    async def get(self, org_id: uuid.UUID) -> Organization:
        org = await self.repo.get(org_id)
        if org is None:
            raise NotFoundError(f"Organization {org_id} not found")
        return org

    async def list(self, *, limit: int, offset: int) -> tuple[list[Organization], int]:
        return await self.repo.list(limit=limit, offset=offset)

    async def create(self, data: OrganizationCreate) -> Organization:
        if await self.repo.get_by_slug(data.slug):
            raise ConflictError(f"Slug '{data.slug}' is already taken")
        try:
            return await self.repo.add(Organization(**data.model_dump()))
        except IntegrityError as exc:  # concurrent insert with the same slug
            raise ConflictError(f"Slug '{data.slug}' is already taken") from exc

    async def update(self, org_id: uuid.UUID, data: OrganizationUpdate) -> Organization:
        org = await self.get(org_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            if field in {"name", "status"} and value is None:
                continue  # non-nullable columns: treat explicit null as "no change"
            setattr(org, field, value)
        await self.repo.session.flush()
        await self.repo.session.refresh(org)
        return org

    async def delete(self, org_id: uuid.UUID) -> None:
        await self.repo.delete(await self.get(org_id))
