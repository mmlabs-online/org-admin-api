"""Admin (Bearer) endpoints for registered apps and their permissions and roles — R3 class 1.

Bearer `aud=admin` + `require_permission` (ADR-0008). Anything on the `admin` app, and any
change across the `is_admin` boundary, also needs `admin:*` (ADM-002b guards in the services).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.api.deps import PageDep, SessionDep
from app.core.config import Settings
from app.core.etag import ETAG_RESPONSE_HEADER, IF_MATCH_DESCRIPTION, parse_if_match, version_etag
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
    AssignmentBatchCreate,
    AssignmentBatchRead,
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
from app.modules.rbac.service import (
    ROLE_DELETE_CONFLICT_REASONS,
    ROLE_PERMISSIONS_CONFLICT_REASONS,
    AppService,
    AssignmentService,
    PermissionService,
    RoleService,
)
from app.modules.releases.body import inline_schema, read_json_body

router = APIRouter(prefix="/apps", tags=["apps"], dependencies=[Depends(current_admin)])


_APP_404 = "Unknown app"
_ROLE_404 = "Unknown app, or role not of that app"
_ADMIN_APP = "On the admin app"
_ASSIGNMENT_BATCH_MAX_BYTES = 16 * 1024
_ASSIGNMENT_BATCH_READ_TIMEOUT_S = 10.0
_ROLE_412 = "Stale If-Match (code precondition_failed); nothing changed; carries the current ETag"


def _with_etag(kwargs: dict[str, Any], code: int, description: str) -> dict[str, Any]:
    kwargs["responses"][code] = {
        "description": description,
        "headers": {"ETag": ETAG_RESPONSE_HEADER},
    }
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
                "One of key, -key, name, -name, created_at, -created_at, member_count, "
                "-member_count ('-' = descending); id ascending breaks ties. name is "
                "case-insensitive and locale-aware (ICU root collation). member_count uses the "
                "same member definition as the member_count field."
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
    **require_permission(
        "admin:apps:write",
        errors={
            409: "Key taken (code=app_key_taken, details[0].reason=app_key_taken) "
            "or origin taken (details[0].reason=origin_taken)"
        },
    ),
)
async def register_app(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, body: AppCreate
) -> AppRead:
    svc = AppService(session)
    return await _app_read_counted(svc, await svc.create(body, actor=principal, ctx=ctx))


@router.get(
    "/{key}",
    response_model=AppRead,
    **require_permission("admin:apps:read", errors={404: _APP_404}),
)
async def get_app(session: SessionDep, key: str) -> AppRead:
    svc = AppService(session)
    return await _app_read_counted(svc, await svc.get(key))


@router.patch(
    "/{key}",
    response_model=AppRead,
    **require_permission(
        "admin:apps:write",
        errors={
            404: _APP_404,
            409: "Admin app invariant, origin already taken, org_mode in use or while a "
            "super-admin role is designated (reason=super_admin_role_designated), or the role "
            "to designate has assignments (reason=role_has_assignments)",
            422: "Invalid designation (super_admin_role_id reasons: admin_app_not_allowed, "
            "unknown_role, org_mode_required, system_role, same_as_default_role, "
            "same_as_org_owner_role, role_has_org_permissions, wildcard_not_allowed)",
        },
        requires_super="conditional",
        super_when="Changing the admin app or the app's super-admin role designation",
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
    **require_permission(
        "admin:apps:write",
        errors={
            404: _APP_404,
            409: "Admin app, app has role assignments, or app has entitlements "
            "(details[0].reason=app_has_entitlements)",
        },
    ),
)
async def delete_app(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, key: str
) -> None:
    await AppService(session).delete(key, actor=principal, ctx=ctx)


# --- permissions -------------------------------------------------------------


@router.get(
    "/{key}/permissions",
    response_model=Page[PermissionRead],
    **require_permission("admin:apps:read", errors={404: _APP_404}),
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
    **require_permission(
        "admin:roles:manage",
        errors={404: _APP_404, 409: "Permission key already exists, or app is disabled"},
        requires_super="conditional",
        super_when="On the admin app, for an is_admin permission, "
        "or for an app-scope permission of an org app",
    ),
)
async def create_permission(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, key: str, body: PermissionCreate
) -> PermissionRead:
    perm = await PermissionService(session).create(key, body, actor=principal, ctx=ctx)
    return PermissionRead.model_validate(perm)


@router.patch(
    "/{key}/permissions/{permission_id}",
    response_model=PermissionRead,
    **require_permission(
        "admin:roles:manage",
        errors={
            404: "Unknown app, or permission not of that app",
            409: "Held by the default role or by an assigned role",
        },
        requires_super="conditional",
        super_when="Changing an admin-app permission, changing is_admin, "
        "or changing scope to app on an org app",
    ),
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
    **require_permission(
        "admin:roles:manage",
        errors={
            404: "Unknown app, or permission not of that app",
            409: "Held by the default role or by an assigned role",
        },
        requires_super="conditional",
        super_when=_ADMIN_APP,
    ),
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
    **require_permission("admin:apps:read", errors={404: _APP_404}),
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
    **_with_etag(
        require_permission("admin:apps:read", errors={404: _ROLE_404}),
        200,
        "Successful Response",
    ),
)
async def get_role_definition(
    session: SessionDep, key: str, role_id: uuid.UUID, response: Response
) -> RoleDefinitionRead:
    read = await RoleService(session).get_definition(key, role_id)
    response.headers["ETag"] = version_etag(read.updated_at)
    return read


@router.get(
    "/{key}/role-definitions/{role_id}/assignments",
    response_model=Page[RoleHolderRead],
    description=(
        "Assignments of one role with their holders. `total` counts assignments, not distinct "
        "users: a user can appear once per org and once at app scope."
    ),
    **require_permission("admin:users:read", errors={404: _ROLE_404}),
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
    **require_permission(
        "admin:roles:manage",
        errors={404: _APP_404, 409: "Role name already exists, or app is disabled"},
        requires_super="conditional",
        super_when=_ADMIN_APP,
    ),
)
async def create_role(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, key: str, body: RoleCreate
) -> RoleRead:
    return await RoleService(session).create(key, body, actor=principal, ctx=ctx)


@router.patch(
    "/{key}/roles/{role_id}",
    response_model=RoleRead,
    **require_permission(
        "admin:roles:manage",
        errors={404: _ROLE_404, 409: "Role name already exists"},
        requires_super="conditional",
        super_when="Renaming an admin-app role",
    ),
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


_ROLE_DELETE_409 = f"code=conflict; details[0].reason: {', '.join(ROLE_DELETE_CONFLICT_REASONS)}"
_ROLE_PERMISSIONS_409 = (
    f"code=conflict; details[0].reason: {', '.join(ROLE_PERMISSIONS_CONFLICT_REASONS)}. "
    "role_has_assignments (an is_admin permission added to a role with assignments) answers "
    "code=role_has_assignments instead"
)


@router.delete(
    "/{key}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **require_permission(
        "admin:roles:manage",
        errors={
            404: _ROLE_404,
            409: _ROLE_DELETE_409,
        },
        requires_super="conditional",
        super_when=_ADMIN_APP,
    ),
)
async def delete_role(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, key: str, role_id: uuid.UUID
) -> None:
    await RoleService(session).delete(key, role_id, actor=principal, ctx=ctx)


@router.put(
    "/{key}/roles/{role_id}/permissions",
    response_model=RoleRead,
    **_with_etag(
        require_permission(
            "admin:roles:manage",
            errors={
                404: _ROLE_404,
                409: _ROLE_PERMISSIONS_409,
                412: _ROLE_412,
            },
            requires_super="conditional",
            super_when="Changing an admin-app role, or adding/removing an is_admin permission",
        ),
        200,
        "Successful Response",
    ),
)
async def replace_role_permissions(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: str,
    role_id: uuid.UUID,
    body: RolePermissionsReplace,
    response: Response,
    if_match: Annotated[
        list[str] | None, Header(alias="If-Match", description=IF_MATCH_DESCRIPTION)
    ] = None,
) -> RoleRead:
    read = await RoleService(session).replace_permissions(
        key, role_id, body, actor=principal, ctx=ctx, precondition=parse_if_match(if_match)
    )
    response.headers["ETag"] = version_etag(read.updated_at)
    return read


_BATCH_KWARGS = require_permission(
    "admin:roles:assign",
    errors={
        404: _ROLE_404,
        409: "The app lock could not be taken in time (conflict, reason changed_concurrently); "
        "nothing written",
        411: "Content-Length required (length_required)",
        413: "Body larger than 16 KiB (payload_too_large)",
        415: "Content-Type must be application/json (unsupported_media_type)",
        422: "validation_error (body shape, 1..100 user_ids), or unprocessable with reason "
        "duplicate_user_ids, admin_app_role, super_admin_role, org_id_not_allowed, unknown_org "
        "or expires_in_past; nothing written",
    },
    requires_super="conditional",
    super_when="Role holding an is_admin permission",
)


def _batch_kwargs() -> dict[str, Any]:
    kwargs = dict(_BATCH_KWARGS)
    kwargs["openapi_extra"] = {
        **kwargs["openapi_extra"],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": inline_schema(AssignmentBatchCreate)}},
        },
    }
    return kwargs


@router.post(
    "/{key}/roles/{role_id}/assignment-batches",
    response_model=AssignmentBatchRead,
    status_code=status.HTTP_200_OK,
    summary="Assign one role to up to 100 users",
    description=(
        "Answers 200 with one result per requested user, in request order (`created`, "
        "`unchanged` or `failed`), even when every user failed; `batch_id` is the request id "
        "(the `X-Request-ID` header and the `request_id` of every audit row of the batch). "
        "The whole batch runs in one transaction under one app lock, held until commit and "
        "bounded by a 5 s lock wait (409 `changed_concurrently`, nothing written). Each user "
        "runs in its own savepoint, so a per-user failure undoes only that user; users are "
        "processed in sorted id order. An unexpected error rolls back the whole batch (5xx, "
        "nothing written). An active holder is `unchanged` and never gets a new `expires_at`; "
        "an expired holder is renewed in place (`created`, same assignment id). A retry gives "
        "`unchanged`. Request-level errors write nothing, except the `denied` audit rows of "
        "403 `admin_wildcard_required`."
    ),
    **_batch_kwargs(),
)
async def create_assignment_batch(
    request: Request,
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: str,
    role_id: uuid.UUID,
) -> AssignmentBatchRead:
    body = await read_json_body(
        request,
        AssignmentBatchCreate,
        cap=_ASSIGNMENT_BATCH_MAX_BYTES,
        timeout_s=_ASSIGNMENT_BATCH_READ_TIMEOUT_S,
    )
    return await AssignmentService(session).assign_batch(
        key, role_id, body, actor=principal, ctx=ctx
    )
