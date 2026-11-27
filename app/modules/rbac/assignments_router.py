"""Admin (Bearer) endpoints for a user's role assignments — R3 class 1.

Bearer `aud=admin` + `require_permission` (ADR-0008). Privileged roles also need `admin:*`, and
nobody grants or revokes their own admin-app roles (ADM-002b guards in `AssignmentService`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.rbac.models import APP_KEY_RE
from app.modules.rbac.schemas import AssignmentCreate, AssignmentRead
from app.modules.rbac.service import (
    REASON_ASSIGNMENT_EXISTS,
    REASON_CHANGED_CONCURRENTLY,
    AssignmentService,
)

router = APIRouter(
    prefix="/users", tags=["role-assignments"], dependencies=[Depends(current_admin)]
)

_USER_404 = "Unknown user"
_PRIVILEGED = "Privileged role (admin-app role, or a role holding an is_admin permission)"


@router.get(
    "/{user_id}/role-assignments",
    response_model=Page[AssignmentRead],
    **require_permission("admin:users:read", errors={404: _USER_404}),
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


_ASSIGN_403 = (
    "Missing permission (permission_denied), admin:* required for a privileged role "
    "(admin_wildcard_required), or the caller's own privileged role (self_assignment_forbidden)"
)
_ASSIGN_409 = (
    f"code=conflict; details[0].reason: {REASON_ASSIGNMENT_EXISTS} (role already actively held "
    f"in this scope, or a racing insert), {REASON_CHANGED_CONCURRENTLY}"
)
_ASSIGN_422 = (
    "validation_error (malformed body), or unprocessable with details[].reason: unknown_role, "
    "org_id_not_allowed, unknown_org, expiry_not_allowed, expires_in_past, "
    "recipient_not_staff_super_admin"
)
_ASSIGN_KWARGS = require_permission(
    "admin:roles:assign",
    errors={
        404: "Unknown user (not_found); an unknown role is 422 unknown_role",
        409: _ASSIGN_409,
        422: _ASSIGN_422,
    },
    requires_super="conditional",
    super_when=_PRIVILEGED,
)
_ASSIGN_KWARGS["responses"][403] = {"description": _ASSIGN_403}


@router.post(
    "/{user_id}/role-assignments",
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
    **_ASSIGN_KWARGS,
)
async def assign_role(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    user_id: uuid.UUID,
    body: AssignmentCreate,
) -> AssignmentRead:
    return await AssignmentService(session).assign(user_id, body, actor=principal, ctx=ctx)


_REVOKE_KWARGS = require_permission(
    "admin:roles:assign",
    errors={404: "Unknown user or assignment", 409: "Last active super-admin"},
    requires_super="conditional",
    super_when=_PRIVILEGED,
)
_REVOKE_KWARGS["responses"][403] = {"description": _ASSIGN_403}


@router.delete(
    "/{user_id}/role-assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **_REVOKE_KWARGS,
)
async def revoke_role(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    user_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> None:
    await AssignmentService(session).revoke(user_id, assignment_id, actor=principal, ctx=ctx)
