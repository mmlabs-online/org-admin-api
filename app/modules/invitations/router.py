"""End-user org invitations (USR-06b). Cookie class (R3 class 2): `__Host-mm_device` + Origin +
`X-MM-App`. No `require_permission`: `<app>:org:invite` is checked org-exact in the service."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status

from app.api.deps import SessionDep
from app.core.config import Settings
from app.core.rate_limit import rate_limit
from app.modules.audit.deps import AuditContextDep
from app.modules.auth.cookies import set_device_cookie
from app.modules.auth.deps import (
    AppContextDep,
    DeviceDep,
    EmailDispatcherDep,
    get_email_hasher,
    get_link_origin,
)
from app.modules.auth.router import DeviceCookieDoc
from app.modules.invitations.schemas import (
    InvitationAccept,
    InvitationAccepted,
    InvitationCreate,
    InvitationCreated,
)
from app.modules.invitations.service import InvitationService
from app.modules.sessions.service import ResolvedDevice

router = APIRouter(prefix="/auth", tags=["auth"])

_ERR: dict[str, Any] = {"description": "Error envelope"}


def _finish(request: Request, response: Response, device: ResolvedDevice) -> None:
    settings: Settings = request.app.state.settings
    if device.new_cookie is not None:
        set_device_cookie(response, device.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"


def _service(request: Request, session: SessionDep) -> InvitationService:
    settings: Settings = request.app.state.settings
    return InvitationService(
        session,
        email_hasher=get_email_hasher(request),
        daily_cap=settings.email_daily_cap,
        ttl_hours=settings.invitation_ttl_hours,
        max_per_inviter_per_day=settings.invitation_max_per_inviter_per_day,
        max_pending_per_org=settings.invitation_max_pending_per_org,
    )


@router.post(
    "/orgs/{org_id}/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("invitation_create")],
    responses={400: _ERR, 401: _ERR, 403: _ERR, 422: _ERR, 429: _ERR},
)
async def create_invitation(
    request: Request,
    response: Response,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    app: AppContextDep,
    link_origin: Annotated[str, Depends(get_link_origin)],
    _cookie: DeviceCookieDoc,
    org_id: uuid.UUID,
    body: InvitationCreate,
    device: DeviceDep,
    ctx: AuditContextDep,
    dispatcher: EmailDispatcherDep,
) -> InvitationCreated:
    """The caller needs `<app>:org:invite` in that org and may grant only a role of the app
    that holds no `is_admin` permission and nothing beyond the caller's own org permissions.

    The same 201 for every target email (no account oracle); the mail is queued after commit
    and the link uses the validated `Origin`. The `admin` app is refused (403).
    """
    created, email = await _service(request, session).create(
        app, device, org_id, body, link_origin, ctx=ctx
    )
    if email is not None:
        background_tasks.add_task(dispatcher.enqueue, email)
    _finish(request, response, device)
    return created


@router.post(
    "/invitations/accept",
    response_model=InvitationAccepted,
    dependencies=[rate_limit("invitation_accept")],
    responses={400: _ERR, 401: _ERR, 403: _ERR, 422: _ERR, 429: _ERR},
)
async def accept_invitation(
    request: Request,
    response: Response,
    session: SessionDep,
    app: AppContextDep,
    _cookie: DeviceCookieDoc,
    body: InvitationAccept,
    device: DeviceDep,
    ctx: AuditContextDep,
) -> InvitationAccepted:
    """Accepts with the one-time token from the mail link. The account must be verified and
    signed in with the invited email address; a token that is unknown, used, expired or
    revoked gets one 422 `token_invalid`."""
    accepted = await _service(request, session).accept(
        app, device, body.account_id, body.token, ctx=ctx
    )
    _finish(request, response, device)
    return accepted
