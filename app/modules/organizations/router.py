import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import PageDep, SessionDep
from app.core.listing import SearchTerm
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.organizations.models import OrganizationStatus
from app.modules.organizations.schemas import (
    DEFAULT_ORGANIZATION_SORT,
    OrganizationCreate,
    OrganizationRead,
    OrganizationSort,
    OrganizationUpdate,
)
from app.modules.organizations.service import OrganizationService

# Admin (Bearer) endpoints — R3 class 1.
router = APIRouter(
    prefix="/organizations", tags=["organizations"], dependencies=[Depends(current_admin)]
)

READ = "admin:organizations:read"
WRITE = "admin:organizations:write"


def _route(key: str, errors: dict[int, str]) -> dict[str, Any]:
    """`require_permission(key)` plus declared error responses (envelope added by the schema)."""
    kwargs = require_permission(key)
    for code, description in errors.items():
        kwargs["responses"][code] = {"description": description}
    return kwargs


_NOT_FOUND = {404: "Organization not found"}


@router.get("", response_model=Page[OrganizationRead], **require_permission(READ))
async def list_organizations(
    session: SessionDep,
    page: PageDep,
    status: Annotated[
        OrganizationStatus | None, Query(description="Only orgs with this status.")
    ] = None,
    q: Annotated[
        SearchTerm | None,
        Query(
            description=(
                "Case-insensitive substring of name or slug; trimmed, 1..100 chars after "
                "trimming; empty or whitespace-only is 422 (omit the parameter instead)."
            )
        ),
    ] = None,
    sort: Annotated[
        OrganizationSort,
        Query(
            description=(
                "One of created_at, -created_at, name, -name, slug, -slug ('-' = descending); "
                "id ascending breaks ties. name follows the database collation."
            )
        ),
    ] = DEFAULT_ORGANIZATION_SORT,
) -> Page[OrganizationRead]:
    items, total = await OrganizationService(session).list(
        status=status, q=q, sort=sort, limit=page.limit, offset=page.offset
    )
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
    **_route(WRITE, {409: "Slug already taken (code conflict)"}),
)
async def create_organization(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, body: OrganizationCreate
) -> OrganizationRead:
    org = await OrganizationService(session).create(body, actor=principal, ctx=ctx)
    return OrganizationRead.model_validate(org)


@router.get("/{org_id}", response_model=OrganizationRead, **_route(READ, _NOT_FOUND))
async def get_organization(session: SessionDep, org_id: uuid.UUID) -> OrganizationRead:
    return OrganizationRead.model_validate(await OrganizationService(session).get(org_id))


@router.patch("/{org_id}", response_model=OrganizationRead, **_route(WRITE, _NOT_FOUND))
async def update_organization(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    org_id: uuid.UUID,
    body: OrganizationUpdate,
) -> OrganizationRead:
    org = await OrganizationService(session).update(org_id, body, actor=principal, ctx=ctx)
    return OrganizationRead.model_validate(org)


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **_route(
        WRITE,
        {
            **_NOT_FOUND,
            409: (
                "Organization still has role assignments (code org_has_assignments) "
                "or entitlements (code org_has_entitlements)"
            ),
        },
    ),
)
async def delete_organization(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, org_id: uuid.UUID
) -> None:
    await OrganizationService(session).delete(org_id, actor=principal, ctx=ctx)
