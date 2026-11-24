"""App-facing org member read — R3 class 4 (a), `org-members:read` app credential (USR-29).

Server-to-server only: no CORS, per-IP limit before auth, per-credential limit after (ADR-0025).
"""

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import AwareDatetime

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.core.rate_limit import enforce_subject_limit, rate_limit
from app.modules.app_credentials.deps import OrgInvitationsCredentialDep, OrgMembersCredentialDep
from app.modules.audit.deps import AuditContextDep
from app.modules.organizations.app_service import AppOrgInvitationService, AppOrgMemberService
from app.modules.organizations.schemas import AppOrgInvitationRead, AppOrgMemberRead
from app.modules.users.models import UserStatus

router = APIRouter(prefix="/apps", tags=["app-orgs"])

_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid app credential"},
    403: {"description": "app_credential_mismatch, app_credential_scope or app_disabled"},
    404: {"description": "org_access_denied (unknown, unrelated or suspended org)"},
    422: {"description": "org_not_supported (org_mode none) or invalid query"},
    429: {"description": "Rate limited"},
}


@router.get(
    "/{key}/orgs/{org_id}/members",
    response_model=Page[AppOrgMemberRead],
    dependencies=[rate_limit("app_org_members")],
    summary="List an org's members for the app (org-members:read app credential)",
    description=(
        "Users holding at least one non-expired org-scoped role of the app in the org, one row "
        "per user, ordered by user id. Disabled users are listed without email and display name. "
        "`updated_since` is additive only: removals and expiry are not visible through it. "
        "Server-to-server only, no CORS; the response is personal data and is never cached."
    ),
    responses=_RESPONSES,
)
async def list_org_members(
    request: Request,
    response: Response,
    session: SessionDep,
    ctx: AuditContextDep,
    caller: OrgMembersCredentialDep,
    page: PageDep,
    key: str,
    org_id: uuid.UUID,
    status: UserStatus | None = None,
    updated_since: AwareDatetime | None = None,
) -> Page[AppOrgMemberRead]:
    enforce_subject_limit(request, "app_org_members_key", caller.key_id)
    items, total = await AppOrgMemberService(session).list(
        caller,
        org_id,
        status=status,
        updated_since=updated_since,
        limit=page.limit,
        offset=page.offset,
        ctx=ctx,
    )
    response.headers["Cache-Control"] = "no-store"
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/{key}/orgs/{org_id}/invitations",
    response_model=Page[AppOrgInvitationRead],
    dependencies=[rate_limit("app_org_members")],
    summary="List an org's pending invitations for the app (org-members:read app credential)",
    description=(
        "Pending invitations of the app in the org: not accepted, not revoked, not expired at "
        "read time (server clock), oldest first. Fields: id, role, created_at, expires_at; never "
        "the invitee email, token or inviter. Pending is not a seat guarantee. Shares the rate "
        "limits of the member read. Server-to-server only, no CORS, never cached."
    ),
    responses=_RESPONSES,
)
async def list_org_invitations(
    request: Request,
    response: Response,
    session: SessionDep,
    ctx: AuditContextDep,
    caller: OrgInvitationsCredentialDep,
    page: PageDep,
    key: str,
    org_id: uuid.UUID,
) -> Page[AppOrgInvitationRead]:
    enforce_subject_limit(request, "app_org_members_key", caller.key_id)
    items, total = await AppOrgInvitationService(session).list(
        caller, org_id, limit=page.limit, offset=page.offset, ctx=ctx
    )
    response.headers["Cache-Control"] = "no-store"
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)
