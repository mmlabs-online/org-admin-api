"""End-user display name (USR-33, D40). Cookie class (R3 class 2): `__Host-mm_device` + Origin +
`X-MM-App`. No `require_permission`: the caller changes only their own account."""

from typing import Any

from fastapi import APIRouter, Request, Response

from app.api.deps import SessionDep
from app.core.config import Settings
from app.core.rate_limit import RateLimit, RateLimiter, rate_limit
from app.modules.audit.deps import AuditContextDep
from app.modules.auth.cookies import set_device_cookie
from app.modules.auth.deps import AppContextDep, DeviceDep
from app.modules.auth.router import DeviceCookieDoc
from app.modules.auth.schemas import AccountRead
from app.modules.users.profile_service import DisplayNameService
from app.modules.users.schemas import DisplayNameSet

router = APIRouter(prefix="/auth", tags=["auth"])

_ERR: dict[str, Any] = {"description": "Error envelope"}


@router.post(
    "/profile",
    response_model=AccountRead,
    dependencies=[rate_limit("profile")],
    responses={401: _ERR, 403: _ERR, 422: _ERR, 429: _ERR},
)
async def set_display_name(
    request: Request,
    response: Response,
    session: SessionDep,
    app: AppContextDep,
    _cookie: DeviceCookieDoc,
    body: DisplayNameSet,
    device: DeviceDep,
    ctx: AuditContextDep,
) -> AccountRead:
    """The signed-in, verified account sets (or, with `null`, clears) its own display name.
    The name is untrusted text: render it as text and key on `sub`, never on the name."""
    settings: Settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.rate_limiter
    user = await DisplayNameService(
        session,
        limiter=limiter,
        user_limit=RateLimit(
            settings.profile_user_rate_limit, settings.profile_user_rate_window_seconds
        ),
    ).set(app, device, body.account_id, body.display_name, ctx=ctx)
    if device.new_cookie is not None:
        set_device_cookie(response, device.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"
    return AccountRead(
        account_id=body.account_id,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        display_name=user.display_name,
    )
