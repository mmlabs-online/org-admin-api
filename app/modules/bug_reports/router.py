"""Admin (Bearer) endpoints for bug-report triage — R3 class 1 (ADM-029, ADR-0020).

Reporter email is PII; reads are not audited (spec §5.12), so every 2xx is `no-store`.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import AwareDatetime

from app.api.deps import PageDep, SessionDep
from app.core.etag import ETAG_RESPONSE_HEADER, IF_MATCH_DESCRIPTION, parse_if_match, version_etag
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.bug_reports.models import BugReportStatus
from app.modules.bug_reports.repository import BugReportView, ListFilter
from app.modules.bug_reports.schemas import (
    BugReportHandler,
    BugReportListItem,
    BugReportRead,
    BugReportReporter,
    BugReportUpdate,
)
from app.modules.bug_reports.staff_service import BugReportStaffService
from app.modules.rbac.models import APP_KEY_RE

router = APIRouter(
    prefix="/bug-reports", tags=["bug-reports"], dependencies=[Depends(current_admin)]
)

READ = "admin:bug-reports:read"
WRITE = "admin:bug-reports:write"
_404 = "Unknown bug report"
_412 = "Stale If-Match (code precondition_failed); nothing changed; carries the current ETag"
_PII = "Contains the reporter's email (PII); the response is `no-store` and reads are not audited."


def _etag_200(kwargs: dict[str, Any]) -> dict[str, Any]:
    kwargs["responses"][200] = {
        "description": "Successful Response",
        "headers": {"ETag": ETAG_RESPONSE_HEADER},
    }
    return kwargs


def _people(v: BugReportView) -> dict[str, Any]:
    return {
        "reporter": BugReportReporter(
            id=v.reporter_id, display_name=v.reporter_display_name, email=v.reporter_email
        ),
        "handler": None
        if v.handler_id is None
        else BugReportHandler(id=v.handler_id, display_name=v.handler_display_name),
    }


def _item(v: BugReportView) -> BugReportListItem:
    return BugReportListItem(
        id=v.id,
        app_key=v.app_key,
        org_id=v.org_id,
        status=BugReportStatus(v.status),
        description_preview=v.description,
        handled_at=v.handled_at,
        created_at=v.created_at,
        updated_at=v.updated_at,
        **_people(v),
    )


def _read(v: BugReportView) -> BugReportRead:
    return BugReportRead(
        id=v.id,
        app_key=v.app_key,
        org_id=v.org_id,
        status=BugReportStatus(v.status),
        description=v.description,
        page_url=v.page_url,
        app_version=v.app_version,
        user_agent=v.user_agent,
        resolution_note=v.resolution_note,
        handled_at=v.handled_at,
        created_at=v.created_at,
        updated_at=v.updated_at,
        **_people(v),
    )


def _detail(response: Response, view: BugReportView) -> BugReportRead:
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = version_etag(view.updated_at)
    return _read(view)


@router.get(
    "",
    response_model=Page[BugReportListItem],
    description="Reports of every app, newest first. " + _PII,
    **require_permission(READ),
)
async def list_bug_reports(
    session: SessionDep,
    page: PageDep,
    response: Response,
    app_key: Annotated[str | None, Query(pattern=APP_KEY_RE)] = None,
    status: Annotated[BugReportStatus | None, Query()] = None,
    reporter_id: Annotated[uuid.UUID | None, Query()] = None,
    created_from: Annotated[
        AwareDatetime | None, Query(alias="from", description="Inclusive.")
    ] = None,
    created_to: Annotated[AwareDatetime | None, Query(alias="to", description="Exclusive.")] = None,
) -> Page[BugReportListItem]:
    f = ListFilter(
        app_key=app_key,
        status=None if status is None else status.value,
        reporter_id=reporter_id,
        created_from=created_from,
        created_to=created_to,
    )
    items, total = await BugReportStaffService(session).list(
        f, limit=page.limit, offset=page.offset
    )
    response.headers["Cache-Control"] = "no-store"
    return Page(items=[_item(i) for i in items], total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/{report_id}",
    response_model=BugReportRead,
    description=_PII,
    **_etag_200(require_permission(READ, errors={404: _404})),
)
async def get_bug_report(
    session: SessionDep, report_id: uuid.UUID, response: Response
) -> BugReportRead:
    return _detail(response, await BugReportStaffService(session).get(report_id))


@router.patch(
    "/{report_id}",
    response_model=BugReportRead,
    description=(
        "Mark done (records the caller as handler), reopen (clears handler and `handled_at`, "
        "keeps the note) and/or edit the note. An absent field is unchanged; "
        "`resolution_note: null` clears it. A request that changes nothing writes nothing."
    ),
    **_etag_200(require_permission(WRITE, errors={404: _404, 412: _412})),
)
async def update_bug_report(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    report_id: uuid.UUID,
    body: BugReportUpdate,
    response: Response,
    if_match: Annotated[
        list[str] | None, Header(alias="If-Match", description=IF_MATCH_DESCRIPTION)
    ] = None,
) -> BugReportRead:
    view = await BugReportStaffService(session).update(
        report_id, body, actor=principal, ctx=ctx, precondition=parse_if_match(if_match)
    )
    return _detail(response, view)
