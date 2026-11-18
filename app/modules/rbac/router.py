"""Admin (Bearer) endpoints for registered apps and their permissions and roles — R3 class 1.

Bearer `aud=admin` + `require_permission` (ADR-0008). Anything on the `admin` app, and any
change across the `is_admin` boundary, also needs `admin:*` (ADM-002b guards in the services).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import PageDep, SessionDep
from app.core.config import Settings
from app.core.listing import SearchTerm
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.rbac.models import App, AppStatus, JoinPolicy, OrgMode
from app.modules.rbac.schemas import (
    DEFAULT_APP_SORT,
    AppCreate,
    AppFields,
    AppRead,
    AppSort,
    AppUpdate,
    PermissionCreate,
    PermissionRead,
    PermissionUpdate,
    RoleCreate,
    RoleDefinitionRead,
    RoleHolderRead,
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


def _with_404(key: str, description: str) -> dict[str, Any]:
    kwargs = require_permission(key)
    kwargs["responses"][404] = {"description": description}
    return kwargs


def _app_read(app: App, member_count: int) -> AppRead:
    return AppRead(**AppFields.model_validate(app).model_dump(), member_count=member_count)


async def _app_read_counted(svc: AppService, app: App) -> AppRead:
    return _app_read(app, (await svc.member_counts([app])).get(app.id, 0))


@router.get("", response_model=Page[AppRead], **require_permission("admin:apps:read"))
async def list_apps(
    session: SessionDep,
    page: PageDep,
    status: Annotated[AppStatus | None, Query(description="Only apps with this status.")] = None,
    join_policy: Annotated[
        JoinPolicy | None, Query(description="Only apps with this join policy.")
    ] = None,
    org_mode: Annotated[OrgMode | None, Query(description="Only apps with this org mode.")] = None,
    q: Annotated[
        SearchTerm | None,
        Query(
            description=(
                "Case-insensitive substring of key or name; trimmed, 1..100 chars after "
                "trimming; empty or whitespace-only is 422 (omit the parameter instead)."
            )
        ),
    ] = None,
    sort: Annotated[
        AppSort,
        Query(
            description=(
                "One of key, -key, name, -name, created_at, -created_at ('-' = descending); "
                "id ascending breaks ties. name follows the database collation."
            )
        ),
    ] = DEFAULT_APP_SORT,
) -> Page[AppRead]:
    svc = AppService(session)
    items, total = await svc.list_apps(
        status=status,
        join_policy=join_policy,
        org_mode=org_mode,
        q=q,
        sort=sort,
        limit=page.limit,
        offset=page.offset,
    )
    counts = await svc.member_counts(items)
    return Page(
        items=[_app_read(a, counts.get(a.id, 0)) for a in items],
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
    svc = AppService(session)
    return await _app_read_counted(svc, await svc.create(body, actor=principal, ctx=ctx))


@router.get("/{key}", response_model=AppRead, **require_permission("admin:apps:read"))
async def get_app(session: SessionDep, key: str) -> AppRead:
    svc = AppService(session)
    return await _app_read_counted(svc, await svc.get(key))


@router.patch(
    "/{key}",
    response_model=AppRead,
    **_with_409(
        "admin:apps:write", "Admin app invariant, origin already taken, or org_mode in use"
    ),
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
    svc = AppService(session)
    app = await svc.update(key, body, actor=principal, ctx=ctx, app_env=settings.app_env)
    return await _app_read_counted(svc, app)


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


@router.get(
    "/{key}/role-definitions",
    response_model=Page[RoleDefinitionRead],
    **require_permission("admin:apps:read"),
)
async def list_role_definitions(
    session: SessionDep, key: str, page: PageDep
) -> Page[RoleDefinitionRead]:
    items, total = await RoleService(session).list_definitions(
        key, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/{key}/role-definitions/{role_id}",
    response_model=RoleDefinitionRead,
    **require_permission("admin:apps:read"),
)
async def get_role_definition(
    session: SessionDep, key: str, role_id: uuid.UUID
) -> RoleDefinitionRead:
    return await RoleService(session).get_definition(key, role_id)


@router.get(
    "/{key}/role-definitions/{role_id}/assignments",
    response_model=Page[RoleHolderRead],
    description=(
        "Assignments of one role with their holders. `total` counts assignments, not distinct "
        "users: a user can appear once per org and once at app scope."
    ),
    **_with_404("admin:users:read", "Unknown app, or role not of that app"),
)
async def list_role_holders(
    session: SessionDep,
    response: Response,
    key: str,
    role_id: uuid.UUID,
    page: PageDep,
    active: Annotated[
        bool | None,
        Query(description="true: not expired; false: expired; omitted: all rows."),
    ] = None,
    org_id: Annotated[
        uuid.UUID | None, Query(description="Only assignments in this organization.")
    ] = None,
) -> Page[RoleHolderRead]:
    items, total = await RoleService(session).list_holders(
        key, role_id, active=active, org_id=org_id, limit=page.limit, offset=page.offset
    )
    response.headers["Cache-Control"] = "no-store"  # rows carry emails
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


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
