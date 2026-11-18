"""Admin (Bearer) endpoints, R3 class 1: the read-only audit log (ADM-004). List and get are not
audited; the CSV export is (ADM-022)."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.actions import ActorKind, AuditAction, AuditOutcome, AuditTarget
from app.modules.audit.deps import AuditContextDep
from app.modules.audit.repository import AuditLogFilter
from app.modules.audit.schemas import AuditLogRead
from app.modules.audit.service import EXPORT_MAX_ROWS, AuditLogService
from app.modules.rbac.models import APP_KEY_RE

router = APIRouter(prefix="/audit-logs", tags=["audit"], dependencies=[Depends(current_admin)])


def _read_route(errors: dict[int, str]) -> dict[str, Any]:
    kwargs = require_permission("admin:audit:read")
    for code, description in errors.items():
        kwargs["responses"][code] = {"description": description}
    return kwargs


def audit_filters(
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_kind: Annotated[ActorKind | None, Query()] = None,
    action: Annotated[AuditAction | None, Query()] = None,
    target_type: Annotated[AuditTarget | None, Query()] = None,
    target_id: Annotated[uuid.UUID | None, Query()] = None,
    app_key: Annotated[str | None, Query(pattern=APP_KEY_RE)] = None,
    org_id: Annotated[
        uuid.UUID | None,
        Query(
            description=(
                "Rows about this organization: the target is this organization, or the row's "
                "`org_id` equals it. Combined with the other filters by AND (e.g. with "
                "`target_type=user`, only user-targeted rows whose `org_id` is this org)."
            )
        ),
    ] = None,
    outcome: Annotated[AuditOutcome | None, Query()] = None,
    occurred_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    occurred_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
) -> AuditLogFilter:
    return AuditLogFilter(
        actor_user_id=actor_user_id,
        actor_kind=None if actor_kind is None else actor_kind.value,
        action=None if action is None else action.value,
        target_type=None if target_type is None else target_type.value,
        target_id=target_id,
        app_key=app_key,
        org_id=org_id,
        outcome=None if outcome is None else outcome.value,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )


AuditFilterDep = Annotated[AuditLogFilter, Depends(audit_filters)]


@router.get("", response_model=Page[AuditLogRead], **require_permission("admin:audit:read"))
async def list_audit_logs(
    session: SessionDep, page: PageDep, filters: AuditFilterDep
) -> Page[AuditLogRead]:
    items, total = await AuditLogService(session).list(
        filters, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[AuditLogRead.model_validate(r) for r in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


def _export_route() -> dict[str, Any]:
    kwargs = require_permission(
        "admin:audit:read", errors={422: "Invalid filter, or from after to"}
    )
    kwargs["responses"][200] = {
        "description": (
            f"The audit log as CSV, newest first, at most {EXPORT_MAX_ROWS:,} rows. When "
            "truncated (`X-Export-Truncated: true`), narrow with `to` = the last row's "
            "`occurred_at`. `limit`/`offset` are not accepted. Each export is audited "
            "(`audit_log.exported`)."
        ),
        "content": {"text/csv": {"schema": {"type": "string"}}},
        "headers": {
            "Content-Disposition": {"schema": {"type": "string"}},
            "X-Export-Truncated": {"schema": {"type": "string", "enum": ["true", "false"]}},
            "X-Export-Row-Count": {"schema": {"type": "integer"}},
            "Cache-Control": {"schema": {"type": "string"}},
        },
    }
    return kwargs


# Declared before `/{audit_log_id}`, which would otherwise capture "export".
@router.get("/export", response_class=Response, **_export_route())
async def export_audit_logs(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, filters: AuditFilterDep
) -> Response:
    result = await AuditLogService(session).export(filters, actor=principal, ctx=ctx)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Response(
        content=result.content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="audit-log-{stamp}.csv"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Export-Truncated": "true" if result.truncated else "false",
            "X-Export-Row-Count": str(result.row_count),
        },
    )


@router.get(
    "/{audit_log_id}",
    response_model=AuditLogRead,
    **_read_route({404: "Audit log entry not found"}),
)
async def get_audit_log(session: SessionDep, audit_log_id: uuid.UUID) -> AuditLogRead:
    return AuditLogRead.model_validate(await AuditLogService(session).get(audit_log_id))
