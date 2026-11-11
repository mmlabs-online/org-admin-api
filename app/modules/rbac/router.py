"""Admin (Bearer) endpoints for registered apps — R3 class 1.

Unauthenticated until ADM-002; mounted only when `admin_rbac_api_enabled` (see `create_app`).
"""

from fastapi import APIRouter, status

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.core.permissions import permission
from app.modules.rbac.schemas import AppCreate, AppRead, AppUpdate
from app.modules.rbac.service import AppService

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("", response_model=Page[AppRead], openapi_extra=permission("admin:apps:read"))
async def list_apps(session: SessionDep, page: PageDep) -> Page[AppRead]:
    items, total = await AppService(session).list(limit=page.limit, offset=page.offset)
    return Page(
        items=[AppRead.model_validate(a) for a in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=AppRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=permission("admin:apps:write"),
)
async def register_app(session: SessionDep, body: AppCreate) -> AppRead:
    return AppRead.model_validate(await AppService(session).create(body))


@router.get("/{key}", response_model=AppRead, openapi_extra=permission("admin:apps:read"))
async def get_app(session: SessionDep, key: str) -> AppRead:
    return AppRead.model_validate(await AppService(session).get(key))


@router.patch("/{key}", response_model=AppRead, openapi_extra=permission("admin:apps:write"))
async def update_app(session: SessionDep, key: str, body: AppUpdate) -> AppRead:
    return AppRead.model_validate(await AppService(session).update(key, body))


@router.delete(
    "/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=permission("admin:apps:write"),
)
async def delete_app(session: SessionDep, key: str) -> None:
    await AppService(session).delete(key)
