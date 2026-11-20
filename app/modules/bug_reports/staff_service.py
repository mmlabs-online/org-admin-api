"""Staff triage of bug reports (ADM-029, ADR-0020). HTTP-free (R1)."""

import logging
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PreconditionFailedError, UnprocessableError
from app.core.etag import IfMatch
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.audit.actions import AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.bug_reports import triage
from app.modules.bug_reports.models import BugReportStatus
from app.modules.bug_reports.repository import BugReportStaffRepository, BugReportView, ListFilter
from app.modules.bug_reports.schemas import BugReportUpdate

logger = logging.getLogger(__name__)


class BugReportStaffService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = BugReportStaffRepository(session)
        self.audit = AuditLogger(session)

    async def list(
        self, f: ListFilter, *, limit: int, offset: int
    ) -> tuple[list[BugReportView], int]:
        if (
            f.created_from is not None
            and f.created_to is not None
            and f.created_from > f.created_to
        ):
            raise UnprocessableError(
                "from must not be after to", details=[{"field": "from", "reason": "after_to"}]
            )
        return await self.repo.page(f, limit=limit, offset=offset)

    async def get(self, report_id: uuid.UUID) -> BugReportView:
        view = await self.repo.get_view(report_id)
        if view is None:
            raise NotFoundError("Bug report not found")
        return view

    async def update(
        self,
        report_id: uuid.UUID,
        data: BugReportUpdate,
        *,
        actor: AdminPrincipal,
        ctx: AuditContext,
        precondition: IfMatch | None = None,
    ) -> BugReportView:
        report = await self.repo.get_for_update(report_id)
        if report is None:
            raise NotFoundError("Bug report not found")
        if (
            precondition is not None
            and not precondition.any
            and report.updated_at not in precondition.versions
        ):
            raise PreconditionFailedError(report.updated_at)
        fields: dict[str, Any] = {}
        if data.status is not None:
            fields["status"] = data.status.value
        if "resolution_note" in data.model_fields_set:
            fields["resolution_note"] = data.resolution_note
        decision = triage.decide(report.status, report.resolution_note, fields)
        if decision.is_noop:
            return await self.get(report_id)
        values = dict(decision.values)
        if decision.status_change is not None:
            if decision.status_change[1] == BugReportStatus.DONE.value:
                values["handled_by"] = actor.user_id
                values["handled_at"] = func.now()
            else:
                values["handled_by"] = None
                values["handled_at"] = None
        await self.repo.apply(report_id, values)
        app_key = await self.repo.app_key(report.app_id)
        for action, changes in triage.audit_entries(decision):
            await self.audit.record(
                action,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.BUG_REPORT, report_id),
                app_key=app_key,
                org_id=None,
                changes=changes,
            )
        logger.info(
            "bug_report_updated report_id=%s status=%s actor=%s",
            report_id,
            values.get("status", report.status),
            actor.user_id,
        )
        return await self.get(report_id)
