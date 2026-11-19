import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.deps import PageDep, SessionDep
from app.core.etag import ETAG_RESPONSE_HEADER, IF_MATCH_DESCRIPTION, parse_if_match, version_etag
from app.core.listing import SearchTerm
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.invitations.admin_service import InvitationAdminService
from app.modules.invitations.schemas import InvitationStatus, OrgInvitationRead
from app.modules.organizations.models import OrganizationStatus
from app.modules.organizations.schemas import (
    DEFAULT_ORGANIZATION_SORT,
    OrganizationCreate,
    OrganizationRead,
    OrganizationSort,
    OrganizationUpdate,
)
from app.modules.organizations.service import OrganizationService
from app.modules.rbac.models import APP_KEY_RE
from app.modules.rbac.schemas import OrgMemberRead
from app.modules.rbac.service import AssignmentService

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
_PRECONDITION = {
    412: "Stale If-Match (code precondition_failed); nothing changed; carries the current ETag"
}


def _with_etag(kwargs: dict[str, Any], code: int, description: str) -> dict[str, Any]:
    kwargs["responses"][code] = {
        "description": description,
        "headers": {"ETag": ETAG_RESPONSE_HEADER},
    }
    return kwargs


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
    **_with_etag(_route(WRITE, {409: "Slug already taken (code conflict)"}), 201, "Created"),
)
async def create_organization(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    body: OrganizationCreate,
    response: Response,
) -> OrganizationRead:
    org = OrganizationRead.model_validate(
        await OrganizationService(session).create(body, actor=principal, ctx=ctx)
    )
    response.headers["ETag"] = version_etag(org.updated_at)
    return org


@router.get(
    "/{org_id}",
    response_model=OrganizationRead,
    **_with_etag(_route(READ, _NOT_FOUND), 200, "Successful Response"),
)
async def get_organization(
    session: SessionDep, org_id: uuid.UUID, response: Response
) -> OrganizationRead:
    org = OrganizationRead.model_validate(await OrganizationService(session).get(org_id))
    response.headers["ETag"] = version_etag(org.updated_at)
    return org


@router.patch(
    "/{org_id}",
    response_model=OrganizationRead,
    **_with_etag(_route(WRITE, {**_NOT_FOUND, **_PRECONDITION}), 200, "Successful Response"),
)
async def update_organization(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    org_id: uuid.UUID,
    body: OrganizationUpdate,
    response: Response,
    if_match: Annotated[
        list[str] | None, Header(alias="If-Match", description=IF_MATCH_DESCRIPTION)
    ] = None,
) -> OrganizationRead:
    org = OrganizationRead.model_validate(
        await OrganizationService(session).update(
            org_id, body, actor=principal, ctx=ctx, precondition=parse_if_match(if_match)
        )
    )
    response.headers["ETag"] = version_etag(org.updated_at)
    return org


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


@router.get(
    "/{org_id}/members",
    response_model=Page[OrgMemberRead],
    description=(
        "Org-scoped role assignments of this organization with their holders. `total` counts "
        "assignments, not distinct users: a user with two roles in the org appears twice."
    ),
    **_route("admin:users:read", _NOT_FOUND),
)
async def list_org_members(
    session: SessionDep,
    response: Response,
    org_id: uuid.UUID,
    page: PageDep,
    app: Annotated[
        str | None, Query(pattern=APP_KEY_RE, description="Only roles of this app key.")
    ] = None,
    active: Annotated[
        bool | None,
        Query(description="true: not expired; false: expired; omitted: all rows."),
    ] = None,
) -> Page[OrgMemberRead]:
    items, total = await AssignmentService(session).list_org_members(
        org_id, app_key=app, active=active, limit=page.limit, offset=page.offset
    )
    response.headers["Cache-Control"] = "no-store"  # rows carry emails
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/{org_id}/invitations",
    response_model=Page[OrgInvitationRead],
    description="Invitations of this organization, newest first. No invitee email or token.",
    **_route(READ, _NOT_FOUND),
)
async def list_org_invitations(
    session: SessionDep,
    response: Response,
    org_id: uuid.UUID,
    page: PageDep,
    status: Annotated[
        InvitationStatus | None, Query(description="Only invitations with this status.")
    ] = None,
) -> Page[OrgInvitationRead]:
    items, total = await InvitationAdminService(session).list_for_org(
        org_id, status=status, limit=page.limit, offset=page.offset
    )
    response.headers["Cache-Control"] = "no-store"
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.delete(
    "/{org_id}/invitations/{invitation_id}",
    status_code=204,
    **_route(
        WRITE,
        {
            404: "Organization or invitation not found",
            409: "Invitation already accepted (code conflict, reason invitation_accepted)",
        },
    ),
)
async def revoke_org_invitation(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    org_id: uuid.UUID,
    invitation_id: uuid.UUID,
) -> None:
    await InvitationAdminService(session).revoke(org_id, invitation_id, actor=principal, ctx=ctx)
