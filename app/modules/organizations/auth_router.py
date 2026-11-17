"""End-user org endpoints (USR-06a). Cookie class (R3 class 2): `__Host-mm_device` + Origin +
`X-MM-App`. No `require_permission`: the app-scope permission is checked in the service."""

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.api.deps import PageDep, SessionDep
from app.core.config import Settings
from app.core.pagination import Page
from app.core.rate_limit import rate_limit
from app.modules.audit.deps import AuditContextDep
from app.modules.auth.cookies import set_device_cookie
from app.modules.auth.deps import AppContextDep, DeviceDep
from app.modules.auth.router import DeviceCookieDoc
from app.modules.organizations.auth_service import OrgSelfService
from app.modules.organizations.schemas import AuthOrgCreate, AuthOrgRead, MyOrgRead
from app.modules.sessions.service import ResolvedDevice

router = APIRouter(prefix="/auth/orgs", tags=["auth"])

_ERR: dict[str, Any] = {"description": "Error envelope"}


def _finish(request: Request, response: Response, device: ResolvedDevice) -> None:
    settings: Settings = request.app.state.settings
    if device.new_cookie is not None:
        set_device_cookie(response, device.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "",
    response_model=AuthOrgRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("org_create")],
    responses={401: _ERR, 403: _ERR, 409: _ERR, 422: _ERR, 429: _ERR},
)
async def create_org(
    request: Request,
    response: Response,
    session: SessionDep,
    app: AppContextDep,
    _cookie: DeviceCookieDoc,
    body: AuthOrgCreate,
    device: DeviceDep,
    ctx: AuditContextDep,
) -> AuthOrgRead:
    """The caller needs the app-scope permission `<app>:org:create`; the creator gets the app's
    org owner role in the new org."""
    settings: Settings = request.app.state.settings
    org = await OrgSelfService(
        session,
        max_per_user=settings.org_create_max_per_user,
        join_reject_cooldown_seconds=settings.join_reject_cooldown_seconds,
    ).create(app, device, body, ctx=ctx)
    _finish(request, response, device)
    return org


@router.get(
    "",
    response_model=Page[MyOrgRead],
    dependencies=[rate_limit("orgs_list")],
    responses={401: _ERR, 403: _ERR, 422: _ERR, 429: _ERR},
)
async def list_my_orgs(
    request: Request,
    response: Response,
    session: SessionDep,
    app: AppContextDep,
    _cookie: DeviceCookieDoc,
    device: DeviceDep,
    page: PageDep,
    account_id: uuid.UUID,
) -> Page[MyOrgRead]:
    """Orgs where the account holds a role of the calling app (own data only)."""
    settings: Settings = request.app.state.settings
    items, total = await OrgSelfService(
        session,
        max_per_user=settings.org_create_max_per_user,
        join_reject_cooldown_seconds=settings.join_reject_cooldown_seconds,
    ).list_mine(app, device, account_id, limit=page.limit, offset=page.offset)
    _finish(request, response, device)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)
