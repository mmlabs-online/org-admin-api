"""Admin (Bearer) endpoints for a user's role assignments — R3 class 1.

Unauthenticated until ADM-002; mounted only when `admin_rbac_api_enabled` (see `create_app`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.core.permissions import permission
from app.modules.rbac.models import APP_KEY_RE
from app.modules.rbac.schemas import AssignmentCreate, AssignmentRead
from app.modules.rbac.service import AssignmentService

router = APIRouter(prefix="/users", tags=["role-assignments"])


@router.get(
    "/{user_id}/role-assignments",
    response_model=Page[AssignmentRead],
    openapi_extra=permission("admin:users:read"),
)
async def list_role_assignments(
    session: SessionDep,
    user_id: uuid.UUID,
    page: PageDep,
    app: Annotated[str | None, Query(pattern=APP_KEY_RE)] = None,
) -> Page[AssignmentRead]:
    items, total = await AssignmentService(session).list(
        user_id, app_key=app, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/{user_id}/role-assignments",
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=permission("admin:roles:assign"),
)
async def assign_role(
    session: SessionDep, user_id: uuid.UUID, body: AssignmentCreate
) -> AssignmentRead:
    return await AssignmentService(session).assign(user_id, body)


@router.delete(
    "/{user_id}/role-assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=permission("admin:roles:assign"),
)
async def revoke_role(session: SessionDep, user_id: uuid.UUID, assignment_id: uuid.UUID) -> None:
    await AssignmentService(session).revoke(user_id, assignment_id)
