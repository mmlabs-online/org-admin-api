"""Admin (Bearer) endpoints — R3 class 1.

Bearer `aud=admin` + `require_permission` (ADR-0008). Status or display-name changes of
privileged users also need `admin:*` (ADM-002b guards in `UserService`). USR-10: staff session
list and revoke, all `admin:*`.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.rbac.models import APP_KEY_RE
from app.modules.releases.body import inline_schema, read_json_body
from app.modules.sessions.schemas import UserSessionRead
from app.modules.sessions.staff_service import StaffSessionService
from app.modules.users.models import User, UserStatus
from app.modules.users.schemas import (
    EmailMatch,
    UserInclude,
    UserRead,
    UserSearch,
    UserUpdate,
    UserWithAccessRead,
)
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(current_admin)])

_SEARCH_MAX_BYTES = 4 * 1024
_SEARCH_READ_TIMEOUT_S = 10.0


async def _users_page(
    service: UserService,
    items: list[User],
    total: int,
    *,
    limit: int,
    offset: int,
    include: list[UserInclude] | None,
) -> Page[UserWithAccessRead] | Page[UserRead]:
    if include is None or UserInclude.ACCESS not in include:
        return Page(
            items=[UserRead.model_validate(u) for u in items],
            total=total,
            limit=limit,
            offset=offset,
        )
    summaries = await service.access_summaries([u.id for u in items])
    return Page(
        items=[
            UserWithAccessRead(
                **UserRead.model_validate(u).model_dump(),
                access=summaries[u.id][0],
                access_truncated=summaries[u.id][1],
            )
            for u in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "",
    response_model=Page[UserWithAccessRead] | Page[UserRead],
    **require_permission(
        "admin:users:read",
        errors={
            422: (
                "Validation failed (code=validation_error: malformed query value such as an "
                "`app` key that fails the key grammar or a `role_id` that is not a UUID) or "
                "refused filter value (code=unprocessable, details[].reason: unknown_app, "
                "unknown_role, role_not_in_app, email_required)."
            )
        },
    ),
)
async def list_users(
    session: SessionDep,
    page: PageDep,
    email: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=254,
            description=(
                "Case-insensitive substring by default; `email_match=exact` for one account. "
                "Prefer `POST /users/search` (keeps the email out of URLs and logs)."
            ),
        ),
    ] = None,
    email_match: Annotated[
        EmailMatch | None,
        Query(
            description=(
                "`contains` (default) or `exact` (trimmed, lower-cased like sign-up; 0 or 1 "
                "item). Without `email` -> 422 `unprocessable`, reason `email_required`."
            )
        ),
    ] = None,
    status: Annotated[UserStatus | None, Query()] = None,
    app: Annotated[
        str | None,
        Query(
            pattern=APP_KEY_RE,
            description=(
                "App key. Only users with at least one active (not expired) assignment of any "
                "role of this app, at app scope or in any organization. Disabled apps are "
                "accepted. Unknown key -> 422 `unprocessable`, reason `unknown_app`."
            ),
        ),
    ] = None,
    role_id: Annotated[
        uuid.UUID | None,
        Query(
            description=(
                "Role id. Only users holding this role actively, at app scope or in any "
                "organization. Unknown -> 422 `unprocessable`, reason `unknown_role`; with "
                "`app`, a role of another app -> reason `role_not_in_app`."
            )
        ),
    ] = None,
    include: Annotated[
        list[UserInclude] | None,
        Query(
            description=(
                "Opt-in extras; only `access` today. `access` adds each user's active role "
                "assignments grouped by app (at most 20 per user, ordered by app key, role key, "
                "app scope before org scope; `access_truncated` when there are more; the full "
                "list is `GET /users/{id}/role-assignments`). Not narrowed by other filters."
            )
        ),
    ] = None,
) -> Page[UserWithAccessRead] | Page[UserRead]:
    service = UserService(session)
    items, total = await service.list(
        email=email,
        email_match=email_match,
        status=status,
        app_key=app,
        role_id=role_id,
        limit=page.limit,
        offset=page.offset,
    )
    return await _users_page(
        service, items, total, limit=page.limit, offset=page.offset, include=include
    )


def _search_kwargs() -> dict[str, Any]:
    kwargs = require_permission(
        "admin:users:read",
        errors={
            411: "Content-Length required (length_required)",
            413: "Body larger than 4 KiB (payload_too_large)",
            415: "Content-Type must be application/json (unsupported_media_type)",
            422: (
                "validation_error (body shape) or unprocessable with reason unknown_app, "
                "unknown_role, role_not_in_app or email_required"
            ),
        },
    )
    kwargs["openapi_extra"] = {
        **kwargs["openapi_extra"],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": inline_schema(UserSearch)}},
        },
    }
    return kwargs


@router.post(
    "/search",
    response_model=Page[UserWithAccessRead] | Page[UserRead],
    status_code=200,
    summary="Search users (filters in the body)",
    description=(
        "Same filters, `include` and pagination as `GET /users`, with the email in the JSON "
        "body so it stays out of URLs and access logs. Answers 200 (a read, not 201); "
        "read-only, no audit entry."
    ),
    **_search_kwargs(),
)
async def search_users(
    request: Request, session: SessionDep
) -> Page[UserWithAccessRead] | Page[UserRead]:
    body = await read_json_body(
        request, UserSearch, cap=_SEARCH_MAX_BYTES, timeout_s=_SEARCH_READ_TIMEOUT_S
    )
    service = UserService(session)
    items, total = await service.list(
        email=body.email,
        email_match=body.email_match,
        status=body.status,
        app_key=body.app,
        role_id=body.role_id,
        limit=body.limit,
        offset=body.offset,
    )
    return await _users_page(
        service, items, total, limit=body.limit, offset=body.offset, include=body.include
    )


@router.get(
    "/{user_id}",
    response_model=UserRead,
    **require_permission("admin:users:read", errors={404: "Unknown user"}),
)
async def get_user(session: SessionDep, user_id: uuid.UUID) -> UserRead:
    return UserRead.model_validate(await UserService(session).get(user_id))


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    **require_permission(
        "admin:users:write",
        errors={404: "Unknown user", 409: "Last active super-admin"},
        requires_super="conditional",
        super_when="Target user holds a privileged role assignment",
    ),
)
async def update_user(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    user_id: uuid.UUID,
    body: UserUpdate,
) -> UserRead:
    """Set status and/or display name (`display_name: null` clears it)."""
    user = await UserService(session).update(user_id, body, actor=principal, ctx=ctx)
    return UserRead.model_validate(user)


_REVOKE_LIMITS = (
    "Revoking sessions stops the user from getting **new** access tokens (`/auth/token` -> 401 "
    "`session_invalid`) and ends the user's admin (`aud=admin`) tokens bound to those sessions "
    "on their next admin request. Tokens of other apps stay valid until `exp`, at most "
    "`ACCESS_TOKEN_TTL_SECONDS` (12 h in production) plus 30 s leeway. It does not stop a "
    "new sign-in with the "
    "password. IP and user agent are recorded when the browser first signed in and are shared "
    "by every account on that browser."
)


def _sessions_route(summary: str) -> dict[str, Any]:
    route = require_permission("admin:*", errors={404: "User or session not found"})
    route["summary"] = summary
    route["description"] = f"{summary}. {_REVOKE_LIMITS}"
    return route


def _staff_sessions(request: Request, session: SessionDep) -> StaffSessionService:
    settings = request.app.state.settings
    return StaffSessionService(
        session,
        idle_days=settings.device_session_idle_days,
        max_days=settings.device_session_max_days,
    )


StaffSessionsDep = Annotated[StaffSessionService, Depends(_staff_sessions)]


@router.get(
    "/{user_id}/sessions",
    response_model=Page[UserSessionRead],
    **_sessions_route("List a user's active account sessions"),
)
async def list_user_sessions(
    service: StaffSessionsDep,
    principal: AdminDep,
    page: PageDep,
    response: Response,
    user_id: uuid.UUID,
) -> Page[UserSessionRead]:
    response.headers["Cache-Control"] = "no-store"
    items, total = await service.list(
        user_id, actor=principal, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[
            UserSessionRead(
                id=i.id,
                created_at=i.created_at,
                last_used_at=i.last_used_at,
                device_last_seen_at=i.device_last_seen_at,
                ip=i.ip,
                user_agent=i.user_agent,
            )
            for i in items
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.delete(
    "/{user_id}/sessions/{session_id}",
    status_code=204,
    **_sessions_route("Revoke one account session of a user"),
)
async def revoke_user_session(
    service: StaffSessionsDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> None:
    await service.revoke_one(user_id, session_id, actor=principal, ctx=ctx)


@router.delete(
    "/{user_id}/sessions",
    status_code=204,
    **_sessions_route("Revoke all account sessions of a user, on every device"),
)
async def revoke_all_user_sessions(
    service: StaffSessionsDep, principal: AdminDep, ctx: AuditContextDep, user_id: uuid.UUID
) -> None:
    await service.revoke_all(user_id, actor=principal, ctx=ctx)
