"""End-user bug report submit (ADM-028, ADR-0019) and attachment upload (ADM-030, ADR-0023).
Cookie class (R3 class 2): `__Host-mm_device` + Origin + `X-MM-App` (not `admin`). No
`require_permission`: membership is checked in the service."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, Response, status
from starlette.requests import ClientDisconnect

from app.api.deps import SessionDep
from app.core.config import Settings
from app.core.rate_limit import RateLimit, RateLimiter, rate_limit
from app.modules.audit.deps import AuditContextDep
from app.modules.auth.cookies import set_device_cookie
from app.modules.auth.deps import DeviceDep, DeviceNoRotateDep
from app.modules.auth.router import DeviceCookieDoc
from app.modules.bug_reports.deps import ReportAppContextDep
from app.modules.bug_reports.errors import AttachmentIncompleteError
from app.modules.bug_reports.schemas import (
    BugReportAttachmentRead,
    BugReportCreate,
    BugReportCreated,
)
from app.modules.bug_reports.service import (
    BugReportAttachmentService,
    BugReportSubmitService,
)

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


_UPLOAD_ERRORS = {
    401: "session_invalid, email_unverified",
    403: "origin_not_allowed, attachment_upload_denied, attachment_upload_closed",
    409: "attachment_limit_reached",
    411: "length_required",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error, attachment_incomplete",
    429: "rate_limited, attachment_upload_busy",
    503: "attachment_storage_unavailable (details.retryable; Retry-After when retryable)",
}
_UPLOAD_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": "The raw image bytes (one file per request); no multipart, no filename.",
        "content": {
            t: {"schema": {"type": "string", "format": "binary"}}
            for t in ("image/png", "image/jpeg", "image/webp", "image/gif")
        },
    }
}


async def _stream(request: Request) -> AsyncIterator[bytes]:
    disconnected = False
    try:
        async for chunk in request.stream():
            yield chunk
    except ClientDisconnect:
        disconnected = True
    if disconnected:
        raise AttachmentIncompleteError


@router.post(
    "/{report_id}/attachments",
    response_model=BugReportAttachmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("bug_report_attachment")],
    responses={
        code: {"description": f"Error envelope: {what}"} for code, what in _UPLOAD_ERRORS.items()
    },
    openapi_extra=_UPLOAD_OPENAPI,
)
async def upload_bug_report_attachment(
    request: Request,
    response: Response,
    session: SessionDep,
    app: ReportAppContextDep,
    _cookie: DeviceCookieDoc,
    report_id: uuid.UUID,
    account_id: uuid.UUID,
    device: DeviceNoRotateDep,
    ctx: AuditContextDep,
) -> BugReportAttachmentRead:
    """The reporter adds one image (PNG, JPEG, WebP or GIF, at most 5 MB, at most 5 per report)
    to their open report within the upload window. The body is the raw file; `Content-Length` is
    required and the type is checked by content, not by header."""
    settings: Settings = request.app.state.settings
    service = BugReportAttachmentService(
        session,
        store=request.app.state.attachment_store,
        slots=request.app.state.attachment_upload_slots,
        limiter=request.app.state.rate_limiter,
        user_limit=RateLimit(
            settings.bug_report_attachment_user_rate_limit,
            settings.bug_report_attachment_user_rate_window_seconds,
        ),
        window_seconds=settings.bug_report_attachment_window_seconds,
    )
    created = await service.upload(
        app,
        device,
        account_id,
        report_id,
        content_type=request.headers.get("content-type"),
        content_length=request.headers.get("content-length"),
        chunks=_stream(request),
        ctx=ctx,
    )
    response.headers["Cache-Control"] = "no-store"
    return created
