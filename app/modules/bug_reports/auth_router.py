"""End-user bug report submit (ADM-028, ADR-0019). Cookie class (R3 class 2): `__Host-mm_device`
+ Origin + `X-MM-App` (not `admin`). No `require_permission`: membership is checked in the
service."""

from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.api.deps import SessionDep
from app.core.config import Settings
from app.core.rate_limit import RateLimit, RateLimiter, rate_limit
from app.modules.audit.deps import AuditContextDep
from app.modules.auth.cookies import set_device_cookie
from app.modules.auth.deps import DeviceDep
from app.modules.auth.router import DeviceCookieDoc
from app.modules.bug_reports.deps import ReportAppContextDep
from app.modules.bug_reports.schemas import BugReportCreate, BugReportCreated
from app.modules.bug_reports.service import BugReportSubmitService

router = APIRouter(prefix="/auth/bug-reports", tags=["auth"])

_ERR: dict[str, Any] = {"description": "Error envelope"}


@router.post(
    "",
    response_model=BugReportCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("bug_report_create")],
    responses={401: _ERR, 403: _ERR, 422: _ERR, 429: _ERR},
)
async def create_bug_report(
    request: Request,
    response: Response,
    session: SessionDep,
    app: ReportAppContextDep,
    _cookie: DeviceCookieDoc,
    body: BugReportCreate,
    device: DeviceDep,
    ctx: AuditContextDep,
) -> BugReportCreated:
    """A signed-in, verified member of the calling app files a text report; the reporter and the
    app come from the session and `X-MM-App`, never from the body."""
    settings: Settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.rate_limiter
    created = await BugReportSubmitService(
        session,
        limiter=limiter,
        user_limit=RateLimit(
            settings.bug_report_user_rate_limit, settings.bug_report_user_rate_window_seconds
        ),
        max_per_day=settings.bug_report_max_per_user_day,
        max_open=settings.bug_report_max_open_per_user_app,
        join_reject_cooldown_seconds=settings.join_reject_cooldown_seconds,
    ).submit(app, device, body, ctx=ctx)
    if device.new_cookie is not None:
        set_device_cookie(response, device.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"
    return created
