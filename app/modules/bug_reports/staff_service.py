"""Staff triage of bug reports (ADM-029, ADR-0020). HTTP-free (R1)."""

import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PreconditionFailedError, UnprocessableError
from app.core.etag import IfMatch
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.attachments.errors import DriveError, DriveErrorCode
from app.modules.attachments.store import AttachmentStore
from app.modules.audit.actions import AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.bug_reports import triage
from app.modules.bug_reports.errors import (
    AttachmentStorageUnavailableError,
    AttachmentUnavailableError,
)
from app.modules.bug_reports.models import BugReportAttachment, BugReportStatus
from app.modules.bug_reports.repository import (
    BugReportAttachmentRepository,
    BugReportStaffRepository,
    BugReportView,
    ListFilter,
)
from app.modules.bug_reports.schemas import BugReportUpdate

logger = logging.getLogger(__name__)


# Drive says the file is gone or unusable: the stable answer is "unavailable", not an outage.
_GONE = frozenset(
    {
        DriveErrorCode.NOT_FOUND,
        DriveErrorCode.NOT_CONFIGURED,
        DriveErrorCode.INVALID_INPUT,
        DriveErrorCode.TOO_LARGE,
    }
)
_RETRYABLE = frozenset({DriveErrorCode.UNAVAILABLE, DriveErrorCode.RATE_LIMITED})


@dataclass(frozen=True)
class AttachmentDownload:
    attachment_id: uuid.UUID
    content_type: str
    size_bytes: int
    body: AsyncIterator[bytes]  # closes the Drive stream when exhausted or closed


class BugReportStaffService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = BugReportStaffRepository(session)
        self.attachments = BugReportAttachmentRepository(session)
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

    async def list_attachments(self, report_id: uuid.UUID) -> Sequence[BugReportAttachment]:
        return await self.attachments.list_for_report(report_id)

    async def open_download(
        self, report_id: uuid.UUID, attachment_id: uuid.UUID, store: AttachmentStore
    ) -> AttachmentDownload:
        """Open the Drive stream and pull its first chunk, so a failure is an error response and
        never a half-sent 200. Not audited (spec OQ28)."""
        row = await self.attachments.get(report_id, attachment_id)
        if row is None:
            raise NotFoundError("Attachment not found")
        if row.file_deleted_at is not None:
            raise AttachmentUnavailableError
        stack = AsyncExitStack()
        code: DriveErrorCode | None = None
        first: bytes | None = None
        chunks: AsyncIterator[bytes] | None = None
        try:
            chunks = await stack.enter_async_context(store.open_read(row.drive_file_id))
            first = await anext(chunks, b"")
        except DriveError as exc:
            code = exc.code
        if code is not None or chunks is None or first is None:
            await stack.aclose()
            code = code or DriveErrorCode.UNAVAILABLE
            logger.warning(
                "bug_report_attachment_read_failed report_id=%s attachment_id=%s code=%s",
                report_id,
                attachment_id,
                code.value,
            )
            if code in _GONE:
                raise AttachmentUnavailableError
            raise AttachmentStorageUnavailableError(retryable=code in _RETRYABLE)
        return AttachmentDownload(
            attachment_id=attachment_id,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            body=_body(stack, chunks, first, row.size_bytes),
        )

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


async def _body(
    stack: AsyncExitStack, chunks: AsyncIterator[bytes], first: bytes, expected: int
) -> AsyncIterator[bytes]:
    """The probed first chunk, then the rest. A body that is not exactly `expected` bytes aborts
    the response instead of ending as a silent short 200."""
    total = 0
    try:
        if first:
            total += len(first)
            yield first
        async for chunk in chunks:
            total += len(chunk)
            if total > expected:
                break
            yield chunk
        if total != expected:
            raise DriveError(DriveErrorCode.UNAVAILABLE)
    finally:
        await stack.aclose()
