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
from app.modules.sessions.schemas import UserSessionRead
from app.modules.sessions.staff_service import StaffSessionService
from app.modules.users.models import UserStatus
from app.modules.users.schemas import UserRead, UserUpdate
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
