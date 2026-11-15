import uuid

from fastapi import APIRouter, Depends, status

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.organizations.schemas import (
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.modules.organizations.service import OrganizationService

# Admin (Bearer) endpoints — R3 class 1.
router = APIRouter(
    prefix="/organizations", tags=["organizations"], dependencies=[Depends(current_admin)]
)

READ = "admin:organizations:read"
WRITE = "admin:organizations:write"


@router.get("", response_model=Page[OrganizationRead], **require_permission(READ))
async def list_organizations(session: SessionDep, page: PageDep) -> Page[OrganizationRead]:
    items, total = await OrganizationService(session).list(limit=page.limit, offset=page.offset)
    return Page(
        items=[OrganizationRead.model_validate(o) for o in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=OrganizationRead,
    status_code=status.HTTP_201_CREATED,
    **require_permission(WRITE),
)
async def create_organization(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, body: OrganizationCreate
) -> OrganizationRead:
    org = await OrganizationService(session).create(body, actor=principal, ctx=ctx)
    return OrganizationRead.model_validate(org)


@router.get("/{org_id}", response_model=OrganizationRead, **require_permission(READ))
async def get_organization(session: SessionDep, org_id: uuid.UUID) -> OrganizationRead:
    return OrganizationRead.model_validate(await OrganizationService(session).get(org_id))


@router.patch("/{org_id}", response_model=OrganizationRead, **require_permission(WRITE))
async def update_organization(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    org_id: uuid.UUID,
    body: OrganizationUpdate,
) -> OrganizationRead:
    org = await OrganizationService(session).update(org_id, body, actor=principal, ctx=ctx)
    return OrganizationRead.model_validate(org)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT, **require_permission(WRITE))
async def delete_organization(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, org_id: uuid.UUID
) -> None:
    await OrganizationService(session).delete(org_id, actor=principal, ctx=ctx)
