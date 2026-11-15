"""Admin (Bearer) endpoints for registered apps and their permissions and roles — R3 class 1.

Bearer `aud=admin` + `require_permission` (ADR-0008). Anything on the `admin` app, and any
change across the `is_admin` boundary, also needs `admin:*` (ADM-002b guards in the services).
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import PageDep, SessionDep
from app.core.config import Settings
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
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

router = APIRouter(prefix="/apps", tags=["apps"], dependencies=[Depends(current_admin)])


def _with_409(key: str, description: str) -> dict[str, Any]:
    kwargs = require_permission(key)
    kwargs["responses"][409] = {"description": description}
    return kwargs


@router.get("", response_model=Page[AppRead], **require_permission("admin:apps:read"))
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
    **_with_409("admin:apps:write", "Key or origin already taken"),
)
async def register_app(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, body: AppCreate
) -> AppRead:
    return AppRead.model_validate(await AppService(session).create(body, actor=principal, ctx=ctx))


@router.get("/{key}", response_model=AppRead, **require_permission("admin:apps:read"))
async def get_app(session: SessionDep, key: str) -> AppRead:
    return AppRead.model_validate(await AppService(session).get(key))


@router.patch(
    "/{key}",
    response_model=AppRead,
    **_with_409("admin:apps:write", "Admin app invariant or origin already taken"),
)
async def update_app(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    request: Request,
    key: str,
    body: AppUpdate,
) -> AppRead:
    settings: Settings = request.app.state.settings
    app = await AppService(session).update(
        key, body, actor=principal, ctx=ctx, app_env=settings.app_env
    )
    return AppRead.model_validate(app)


@router.delete(
    "/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    **require_permission("admin:apps:write"),
)
async def delete_app(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, key: str
) -> None:
    await AppService(session).delete(key, actor=principal, ctx=ctx)


# --- permissions -------------------------------------------------------------


@router.get(
    "/{key}/permissions",
    response_model=Page[PermissionRead],
    **require_permission("admin:apps:read"),
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
    **require_permission("admin:roles:manage"),
)
async def create_permission(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, key: str, body: PermissionCreate
) -> PermissionRead:
    perm = await PermissionService(session).create(key, body, actor=principal, ctx=ctx)
    return PermissionRead.model_validate(perm)


@router.patch(
    "/{key}/permissions/{permission_id}",
    response_model=PermissionRead,
    **_with_409("admin:roles:manage", "Held by the default role or by an assigned role"),
)
async def update_permission(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: str,
    permission_id: uuid.UUID,
    body: PermissionUpdate,
) -> PermissionRead:
    perm = await PermissionService(session).update(
        key, permission_id, body, actor=principal, ctx=ctx
    )
    return PermissionRead.model_validate(perm)


@router.delete(
    "/{key}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **require_permission("admin:roles:manage"),
)
async def delete_permission(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: str,
    permission_id: uuid.UUID,
) -> None:
    await PermissionService(session).delete(key, permission_id, actor=principal, ctx=ctx)


# --- roles -------------------------------------------------------------------


@router.post(
    "/{key}/roles",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    **require_permission("admin:roles:manage"),
)
async def create_role(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, key: str, body: RoleCreate
) -> RoleRead:
    return await RoleService(session).create(key, body, actor=principal, ctx=ctx)


@router.patch(
    "/{key}/roles/{role_id}",
    response_model=RoleRead,
    **require_permission("admin:roles:manage"),
)
async def update_role(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: str,
    role_id: uuid.UUID,
    body: RoleUpdate,
) -> RoleRead:
    return await RoleService(session).update(key, role_id, body, actor=principal, ctx=ctx)


@router.delete(
    "/{key}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **require_permission("admin:roles:manage"),
)
async def delete_role(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, key: str, role_id: uuid.UUID
) -> None:
    await RoleService(session).delete(key, role_id, actor=principal, ctx=ctx)


@router.put(
    "/{key}/roles/{role_id}/permissions",
    response_model=RoleRead,
    **_with_409("admin:roles:manage", "Default/super-admin role rule or role has assignments"),
)
async def replace_role_permissions(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: str,
    role_id: uuid.UUID,
    body: RolePermissionsReplace,
) -> RoleRead:
    return await RoleService(session).replace_permissions(
        key, role_id, body, actor=principal, ctx=ctx
    )
