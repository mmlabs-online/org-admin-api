"""Admin (Bearer) endpoints for registered apps and their permissions and roles — R3 class 1.

Unauthenticated until ADM-002; mounted only when `admin_rbac_api_enabled` (see `create_app`).
"""

import uuid

from fastapi import APIRouter, status

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.core.permissions import permission
from app.modules.rbac.schemas import (
    AppCreate,
    AppRead,
    AppUpdate,
    PermissionCreate,
    PermissionRead,
    PermissionUpdate,
    RoleCreate,
    RolePermissionsReplace,
    RoleRead,
    RoleUpdate,
)
from app.modules.rbac.service import AppService, PermissionService, RoleService

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("", response_model=Page[AppRead], openapi_extra=permission("admin:apps:read"))
async def list_apps(session: SessionDep, page: PageDep) -> Page[AppRead]:
    items, total = await AppService(session).list_apps(limit=page.limit, offset=page.offset)
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


# --- permissions -------------------------------------------------------------


@router.get(
    "/{key}/permissions",
    response_model=Page[PermissionRead],
    openapi_extra=permission("admin:apps:read"),
)
async def list_permissions(session: SessionDep, key: str, page: PageDep) -> Page[PermissionRead]:
    items, total = await PermissionService(session).list_permissions(
        key, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[PermissionRead.model_validate(p) for p in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/{key}/permissions",
    response_model=PermissionRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=permission("admin:roles:manage"),
)
async def create_permission(
    session: SessionDep, key: str, body: PermissionCreate
) -> PermissionRead:
    return PermissionRead.model_validate(await PermissionService(session).create(key, body))


@router.patch(
    "/{key}/permissions/{permission_id}",
    response_model=PermissionRead,
    openapi_extra=permission("admin:roles:manage"),
)
async def update_permission(
    session: SessionDep, key: str, permission_id: uuid.UUID, body: PermissionUpdate
) -> PermissionRead:
    return PermissionRead.model_validate(
        await PermissionService(session).update(key, permission_id, body)
    )


@router.delete(
    "/{key}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=permission("admin:roles:manage"),
)
async def delete_permission(session: SessionDep, key: str, permission_id: uuid.UUID) -> None:
    await PermissionService(session).delete(key, permission_id)


# --- roles -------------------------------------------------------------------


@router.post(
    "/{key}/roles",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=permission("admin:roles:manage"),
)
async def create_role(session: SessionDep, key: str, body: RoleCreate) -> RoleRead:
    return await RoleService(session).create(key, body)


@router.patch(
    "/{key}/roles/{role_id}",
    response_model=RoleRead,
    openapi_extra=permission("admin:roles:manage"),
)
async def update_role(
    session: SessionDep, key: str, role_id: uuid.UUID, body: RoleUpdate
) -> RoleRead:
    return await RoleService(session).update(key, role_id, body)


@router.delete(
    "/{key}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=permission("admin:roles:manage"),
)
async def delete_role(session: SessionDep, key: str, role_id: uuid.UUID) -> None:
    await RoleService(session).delete(key, role_id)


@router.put(
    "/{key}/roles/{role_id}/permissions",
    response_model=RoleRead,
    openapi_extra=permission("admin:roles:manage"),
)
async def replace_role_permissions(
    session: SessionDep, key: str, role_id: uuid.UUID, body: RolePermissionsReplace
) -> RoleRead:
    return await RoleService(session).replace_permissions(key, role_id, body)
