"""Admin (Bearer) endpoints — R3 class 1.

Bearer `aud=admin` + `require_permission` (ADR-0008). Status changes of privileged users also
need `admin:*` (ADM-002b guards in `UserService.set_status`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.users.models import UserStatus
from app.modules.users.schemas import UserRead, UserStatusUpdate
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(current_admin)])


@router.get("", response_model=Page[UserRead], **require_permission("admin:users:read"))
async def list_users(
    session: SessionDep,
    page: PageDep,
    email: Annotated[str | None, Query(min_length=1, max_length=254)] = None,
    status: Annotated[UserStatus | None, Query()] = None,
) -> Page[UserRead]:
    items, total = await UserService(session).list(
        email=email, status=status, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[UserRead.model_validate(u) for u in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{user_id}", response_model=UserRead, **require_permission("admin:users:read"))
async def get_user(session: SessionDep, user_id: uuid.UUID) -> UserRead:
    return UserRead.model_validate(await UserService(session).get(user_id))


_WRITE = require_permission("admin:users:write")
_WRITE["responses"][409] = {"description": "Last active super-admin"}


@router.patch("/{user_id}", response_model=UserRead, **_WRITE)
async def update_user_status(
    session: SessionDep, principal: AdminDep, user_id: uuid.UUID, body: UserStatusUpdate
) -> UserRead:
    user = await UserService(session).set_status(user_id, body.status, actor=principal)
    return UserRead.model_validate(user)
