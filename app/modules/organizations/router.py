import uuid

from fastapi import APIRouter, status

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.organizations.schemas import (
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.modules.organizations.service import OrganizationService

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("", response_model=Page[OrganizationRead])
async def list_organizations(session: SessionDep, page: PageDep) -> Page[OrganizationRead]:
    items, total = await OrganizationService(session).list(limit=page.limit, offset=page.offset)
    return Page(
        items=[OrganizationRead.model_validate(o) for o in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
async def create_organization(session: SessionDep, body: OrganizationCreate) -> OrganizationRead:
    org = await OrganizationService(session).create(body)
    return OrganizationRead.model_validate(org)


@router.get("/{org_id}", response_model=OrganizationRead)
async def get_organization(session: SessionDep, org_id: uuid.UUID) -> OrganizationRead:
    return OrganizationRead.model_validate(await OrganizationService(session).get(org_id))


@router.patch("/{org_id}", response_model=OrganizationRead)
async def update_organization(
    session: SessionDep, org_id: uuid.UUID, body: OrganizationUpdate
) -> OrganizationRead:
    org = await OrganizationService(session).update(org_id, body)
    return OrganizationRead.model_validate(org)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(session: SessionDep, org_id: uuid.UUID) -> None:
    await OrganizationService(session).delete(org_id)
