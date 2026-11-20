"""Admin (Bearer) endpoints for deployer credentials — R3 class 1 (ADR-0021 §2, §9).

All three need `admin:*`. The credential value is returned once, by `POST`, with
`Cache-Control: no-store`.
"""

import uuid

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.deployer_credentials.schemas import (
    DeployerCredentialIssued,
    DeployerCredentialRead,
)
from app.modules.deployer_credentials.service import DeployerCredentialService

router = APIRouter(
    prefix="/deployer-credentials",
    tags=["deployer-credentials"],
    dependencies=[Depends(current_admin)],
)


@router.post(
    "",
    response_model=DeployerCredentialIssued,
    status_code=status.HTTP_201_CREATED,
    **require_permission("admin:*", errors={409: "Credential limit (details[0].reason)"}),
)
async def issue_credential(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, response: Response
) -> DeployerCredentialIssued:
    issued = await DeployerCredentialService(session).issue(actor=principal, ctx=ctx)
    response.headers["Cache-Control"] = "no-store"
    return DeployerCredentialIssued.model_validate(issued)


@router.get(
    "",
    response_model=Page[DeployerCredentialRead],
    **require_permission("admin:*"),
)
async def list_credentials(session: SessionDep, page: PageDep) -> Page[DeployerCredentialRead]:
    items, total = await DeployerCredentialService(session).list(
        limit=page.limit, offset=page.offset
    )
    return Page(
        items=[DeployerCredentialRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **require_permission("admin:*", errors={404: "Credential not found"}),
)
async def revoke_credential(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, credential_id: uuid.UUID
) -> None:
    await DeployerCredentialService(session).revoke(credential_id, actor=principal, ctx=ctx)
