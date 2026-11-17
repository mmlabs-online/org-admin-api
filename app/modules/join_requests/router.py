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


def _with_409(description: str) -> dict[str, Any]:
    kwargs = require_permission("admin:roles:assign")
    kwargs["responses"][409] = {"description": description}
    return kwargs


@router.get(
    "/{key}/join-requests",
    response_model=Page[JoinRequestRead],
    **require_permission("admin:users:read"),
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
    **_with_409(
        "not pending, app disabled, policy changed, user disabled/unverified or default role "
        "unusable (details[0].reason)"
    ),
)
async def approve_join_request(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: str,
    request_id: uuid.UUID,
) -> JoinRequestRead:
    """Grants the app's current default role at app scope."""
    return await JoinRequestService(session).approve(key, request_id, actor=principal, ctx=ctx)


@router.post(
    "/{key}/join-requests/{request_id}/reject",
    response_model=JoinRequestRead,
    **_with_409("not pending (details[0].reason)"),
)
async def reject_join_request(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: str,
    request_id: uuid.UUID,
) -> JoinRequestRead:
    return await JoinRequestService(session).reject(key, request_id, actor=principal, ctx=ctx)
