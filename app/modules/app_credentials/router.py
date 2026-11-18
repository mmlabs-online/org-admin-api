"""Admin (Bearer) endpoints for app credentials — R3 class 1 (ADR-0016 §5).

The credential value is returned once, by `POST`, with `Cache-Control: no-store`.
"""

import uuid

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.app_credentials.schemas import AppCredentialIssued, AppCredentialRead
from app.modules.app_credentials.service import AppCredentialService
from app.modules.audit.deps import AuditContextDep

router = APIRouter(prefix="/apps", tags=["app-credentials"], dependencies=[Depends(current_admin)])


@router.post(
    "/{key}/credentials",
    response_model=AppCredentialIssued,
    status_code=status.HTTP_201_CREATED,
    **require_permission(
        "admin:apps:write",
        errors={
            404: "Unknown app",
            409: "admin app, disabled app or credential limit (details[0].reason)",
        },
        requires_super="always",
    ),
)
async def issue_credential(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, response: Response, key: str
) -> AppCredentialIssued:
    issued = await AppCredentialService(session).issue(key, actor=principal, ctx=ctx)
    response.headers["Cache-Control"] = "no-store"
    return AppCredentialIssued.model_validate(issued)


@router.get(
    "/{key}/credentials",
    response_model=Page[AppCredentialRead],
    **require_permission("admin:apps:read", errors={404: "Unknown app"}),
)
async def list_credentials(session: SessionDep, key: str, page: PageDep) -> Page[AppCredentialRead]:
    items, total = await AppCredentialService(session).list(
        key, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[AppCredentialRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.delete(
    "/{key}/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **require_permission("admin:apps:write", errors={404: "Unknown app, or credential not of it"}),
)
async def revoke_credential(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: str,
    credential_id: uuid.UUID,
) -> None:
    await AppCredentialService(session).revoke(key, credential_id, actor=principal, ctx=ctx)
