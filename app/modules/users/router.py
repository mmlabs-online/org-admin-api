"""Admin (Bearer) endpoints — R3 class 1.

Unauthenticated until ADM-002; mounted only when `admin_users_api_enabled` (see `create_app`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.core.permissions import permission
from app.modules.users.models import UserStatus
from app.modules.users.schemas import UserRead, UserStatusUpdate
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserRead], openapi_extra=permission("admin:users:read"))
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


@router.get("/{user_id}", response_model=UserRead, openapi_extra=permission("admin:users:read"))
async def get_user(session: SessionDep, user_id: uuid.UUID) -> UserRead:
    return UserRead.model_validate(await UserService(session).get(user_id))


@router.patch("/{user_id}", response_model=UserRead, openapi_extra=permission("admin:users:write"))
async def update_user_status(
    session: SessionDep, user_id: uuid.UUID, body: UserStatusUpdate
) -> UserRead:
    return UserRead.model_validate(await UserService(session).set_status(user_id, body.status))
