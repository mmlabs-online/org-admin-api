"""Admin (Bearer) endpoints for org entitlements — R3 class 1 (ADM-006)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Response, status

from app.api.deps import PageDep, SessionDep
from app.core.etag import ETAG_RESPONSE_HEADER, IF_MATCH_DESCRIPTION, parse_if_match, version_etag
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.entitlements.schemas import EntitlementCreate, EntitlementRead, EntitlementUpdate
from app.modules.entitlements.service import EntitlementService

router = APIRouter(
    prefix="/organizations/{org_id}/entitlements",
    tags=["entitlements"],
    dependencies=[Depends(current_admin)],
)

READ = "admin:entitlements:read"
WRITE = "admin:entitlements:write"


_ORG_404 = "Unknown organization"
_ENT_404 = "Unknown organization, or entitlement not of it"
_ENT_412 = "Stale If-Match (code precondition_failed); nothing changed; carries the current ETag"


def _with_etag(kwargs: dict[str, Any], code: int, description: str) -> dict[str, Any]:
    kwargs["responses"][code] = {
        "description": description,
        "headers": {"ETag": ETAG_RESPONSE_HEADER},
    }
    return kwargs


@router.get(
    "", response_model=Page[EntitlementRead], **require_permission(READ, errors={404: _ORG_404})
)
async def list_entitlements(
    session: SessionDep, page: PageDep, org_id: uuid.UUID, active: bool | None = None
) -> Page[EntitlementRead]:
    items, total = await EntitlementService(session).list(
        org_id, active=active, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[EntitlementRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=EntitlementRead,
    status_code=status.HTTP_201_CREATED,
    **_with_etag(
        require_permission(
            WRITE,
            errors={
                404: "Unknown organization, or unknown app (details[0].reason=unknown_app)",
                409: "admin app, disabled app, org_mode none or duplicate (details[0].reason)",
            },
        ),
        201,
        "Created",
    ),
)
async def grant_entitlement(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    org_id: uuid.UUID,
    body: EntitlementCreate,
    response: Response,
) -> EntitlementRead:
    view = await EntitlementService(session).grant(org_id, body, actor=principal, ctx=ctx)
    response.headers["ETag"] = version_etag(view.updated_at)
    return EntitlementRead.model_validate(view)


@router.get(
    "/{entitlement_id}",
    response_model=EntitlementRead,
    **_with_etag(require_permission(READ, errors={404: _ENT_404}), 200, "Successful Response"),
)
async def get_entitlement(
    session: SessionDep, org_id: uuid.UUID, entitlement_id: uuid.UUID, response: Response
) -> EntitlementRead:
    view = await EntitlementService(session).get(org_id, entitlement_id)
    response.headers["ETag"] = version_etag(view.updated_at)
    return EntitlementRead.model_validate(view)


@router.patch(
    "/{entitlement_id}",
    response_model=EntitlementRead,
    **_with_etag(
        require_permission(WRITE, errors={404: _ENT_404, 412: _ENT_412}),
        200,
        "Successful Response",
    ),
)
async def update_entitlement(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    org_id: uuid.UUID,
    entitlement_id: uuid.UUID,
    body: EntitlementUpdate,
    response: Response,
    if_match: Annotated[
        list[str] | None, Header(alias="If-Match", description=IF_MATCH_DESCRIPTION)
    ] = None,
) -> EntitlementRead:
    view = await EntitlementService(session).update(
        org_id,
        entitlement_id,
        body,
        actor=principal,
        ctx=ctx,
        precondition=parse_if_match(if_match),
    )
    response.headers["ETag"] = version_etag(view.updated_at)
    return EntitlementRead.model_validate(view)


@router.delete(
    "/{entitlement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **require_permission(WRITE, errors={404: _ENT_404}),
)
async def revoke_entitlement(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    org_id: uuid.UUID,
    entitlement_id: uuid.UUID,
) -> None:
    await EntitlementService(session).revoke(org_id, entitlement_id, actor=principal, ctx=ctx)
