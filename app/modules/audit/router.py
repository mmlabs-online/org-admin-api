"""Admin (Bearer) endpoint, R3 class 1: the read-only audit log (ADM-004). Not audited itself."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import current_admin, require_permission
from app.modules.audit.actions import ActorKind, AuditAction, AuditOutcome, AuditTarget
from app.modules.audit.repository import AuditLogFilter
from app.modules.audit.schemas import AuditLogRead
from app.modules.audit.service import AuditLogService
from app.modules.rbac.models import APP_KEY_RE

router = APIRouter(prefix="/audit-logs", tags=["audit"], dependencies=[Depends(current_admin)])


@router.get("", response_model=Page[AuditLogRead], **require_permission("admin:audit:read"))
async def list_audit_logs(
    session: SessionDep,
    page: PageDep,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_kind: Annotated[ActorKind | None, Query()] = None,
    action: Annotated[AuditAction | None, Query()] = None,
    target_type: Annotated[AuditTarget | None, Query()] = None,
    target_id: Annotated[uuid.UUID | None, Query()] = None,
    app_key: Annotated[str | None, Query(pattern=APP_KEY_RE)] = None,
    outcome: Annotated[AuditOutcome | None, Query()] = None,
    occurred_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    occurred_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
) -> Page[AuditLogRead]:
    filters = AuditLogFilter(
        actor_user_id=actor_user_id,
        actor_kind=None if actor_kind is None else actor_kind.value,
        action=None if action is None else action.value,
        target_type=None if target_type is None else target_type.value,
        target_id=target_id,
        app_key=app_key,
        outcome=None if outcome is None else outcome.value,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    items, total = await AuditLogService(session).list(
        filters, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[AuditLogRead.model_validate(r) for r in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
