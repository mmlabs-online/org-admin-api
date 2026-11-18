"""Admin (Bearer) endpoints for join requests of `approval` apps — R3 class 1 (USR-03g)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.join_requests.schemas import JoinRequestRead, JoinRequestStatusFilter
from app.modules.join_requests.service import JoinRequestService

router = APIRouter(prefix="/apps", tags=["join-requests"], dependencies=[Depends(current_admin)])


def _assign(description: str) -> dict[str, Any]:
    return require_permission(
        "admin:roles:assign",
        errors={404: "Unknown app, or request not of that app", 409: description},
    )


@router.get(
    "/{key}/join-requests",
    response_model=Page[JoinRequestRead],
    **require_permission("admin:users:read", errors={404: "Unknown app"}),
)
async def list_join_requests(
    session: SessionDep,
    response: Response,
    key: str,
    page: PageDep,
    status: Annotated[JoinRequestStatusFilter, Query()] = "pending",
) -> Page[JoinRequestRead]:
    items, total = await JoinRequestService(session).list(
        key, status, limit=page.limit, offset=page.offset
    )
    response.headers["Cache-Control"] = "no-store"  # rows carry emails
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/{key}/join-requests/{request_id}/approve",
    response_model=JoinRequestRead,
    **_assign(
        "not pending, app disabled, policy changed, user disabled/unverified or default role "
        "unusable (details[0].reason)"
    ),
)
async def approve_join_request(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    response: Response,
    key: str,
    request_id: uuid.UUID,
) -> JoinRequestRead:
    """Grants the app's current default role at app scope."""
    result = await JoinRequestService(session).approve(key, request_id, actor=principal, ctx=ctx)
    response.headers["Cache-Control"] = "no-store"  # body carries an email
    return result


@router.post(
    "/{key}/join-requests/{request_id}/reject",
    response_model=JoinRequestRead,
    **_assign("not pending (details[0].reason)"),
)
async def reject_join_request(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    response: Response,
    key: str,
    request_id: uuid.UUID,
) -> JoinRequestRead:
    result = await JoinRequestService(session).reject(key, request_id, actor=principal, ctx=ctx)
    response.headers["Cache-Control"] = "no-store"  # body carries an email
    return result
