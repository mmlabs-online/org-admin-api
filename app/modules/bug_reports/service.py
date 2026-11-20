"""Bug-report submit (ADM-028, ADR-0019). Cookie-class caller (R3 class 2)."""

import asyncio
import logging
import math
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    LengthRequiredError,
    PayloadTooLargeError,
    RateLimitedError,
    UnsupportedMediaTypeError,
)
from app.core.rate_limit import RateLimit, RateLimiter
from app.modules.attachments.errors import DriveError, DriveErrorCode
from app.modules.attachments.store import MAX_ATTACHMENT_BYTES, AttachmentStore, ImageType
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.service import AuditLogger
from app.modules.bug_reports import image_types
from app.modules.bug_reports.errors import (
    AttachmentLimitError,
    AttachmentStorageUnavailableError,
    AttachmentUploadBusyError,
    AttachmentUploadClosedError,
    AttachmentUploadDeniedError,
    BugReportDailyLimitError,
    BugReportOpenLimitError,
)
from app.modules.bug_reports.models import BugReport, BugReportAttachment, BugReportStatus
from app.modules.bug_reports.repository import BugReportAttachmentRepository, BugReportRepository
from app.modules.bug_reports.schemas import (
    BugReportAttachmentRead,
    BugReportCreate,
    BugReportCreated,
)
from app.modules.bug_reports.upload import read_exact
from app.modules.membership.service import join_available
from app.modules.rbac.authz import AuthzService
from app.modules.rbac.models import App, OrgMode
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import ResolvedDevice
from app.modules.tokens.errors import (
    EmailUnverifiedError,
    NotMemberError,
    OrgAccessDeniedError,
    OrgNotSupportedError,
)
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)


async def resolve_reporter(
    session: AsyncSession, device: ResolvedDevice, account_id: uuid.UUID
) -> User:
    """The active, verified user behind `account_id` on this device (shared by submit and the
    attachment upload). No `headers`: the cookie is not cleared, other accounts stay signed in."""
    account = await SessionRepository(session).get_active_account(
        device.device_id, account_id, created_before=device.accounts_created_before
    )
    if account is None:
        raise SessionInvalidError("Session is invalid")
    user = await UserRepository(session).get_for_share(account.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise SessionInvalidError("Session is invalid")
    if user.email_verified_at is None:
        raise EmailUnverifiedError("Email address is not verified")
    return user


class BugReportSubmitService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        limiter: RateLimiter,
        user_limit: RateLimit,
        max_per_day: int,
        max_open: int,
        join_reject_cooldown_seconds: int,
    ) -> None:
        self.session = session
        self.limiter = limiter
        self.user_limit = user_limit
        self.max_per_day = max_per_day
        self.max_open = max_open
        self.join_reject_cooldown_seconds = join_reject_cooldown_seconds
        self.repo = BugReportRepository(session)
        self.authz = AuthzService(session)
        self.audit = AuditLogger(session)

    async def submit(
        self, app: App, device: ResolvedDevice, data: BugReportCreate, *, ctx: AuditContext
    ) -> BugReportCreated:
        user = await resolve_reporter(self.session, device, data.account_id)
        if not await self.authz.effective_roles(user.id, app.key):
            available = await join_available(
                self.session,
                app,
                user.id,
                reject_cooldown_seconds=self.join_reject_cooldown_seconds,
            )
            raise NotMemberError("Not a member of this app", details={"join_available": available})
        if data.org_id is not None:
            if app.org_mode == OrgMode.NONE.value:
                raise OrgNotSupportedError(
                    "This app does not use organizations",
                    details=[{"field": "org_id", "reason": "not_supported"}],
                )
            # Org-exact; unknown, suspended and foreign orgs get one answer (no oracle).
            if not await self.authz.org_role_keys(user.id, app.key, data.org_id):
                raise OrgAccessDeniedError("No access to this organization", details={})

        retry_after = self.limiter.hit("bug_report_user", str(user.id), self.user_limit)
        if retry_after is not None:
            raise RateLimitedError(retry_after)

        await self.repo.lock_reporter(user.id)
        window = await self.repo.daily_window(user.id)
        if window.count >= self.max_per_day:
            oldest = window.oldest or window.now  # never None when count >= 1
            wait = (oldest + timedelta(hours=24) - window.now).total_seconds()
            raise BugReportDailyLimitError(max(1, math.ceil(wait)))
        if await self.repo.count_open(user.id, app.id) >= self.max_open:
            raise BugReportOpenLimitError(self.max_open)

        report = await self.repo.add(
            BugReport(
                app_id=app.id,
                user_id=user.id,
                org_id=data.org_id,
                description=data.description,
                page_url=data.page_url,
                app_version=data.app_version,
                user_agent=data.user_agent,
                status=BugReportStatus.OPEN.value,
            )
        )
        # Ids and counts only, never the description, context fields or attachment content.
        await self.audit.record(
            AuditAction.BUG_REPORT_CREATED,
            actor=EndUser(user.id),
            ctx=ctx,
            target=(AuditTarget.BUG_REPORT, report.id),
            app_key=app.key,
            org_id=None,
            changes={"attachment_count": 0},
        )
        logger.info(
            "bug_report_created report_id=%s app=%s user_id=%s", report.id, app.key, user.id
        )
        return BugReportCreated(
            id=report.id, status=BugReportStatus(report.status), created_at=report.created_at
        )


MAX_ATTACHMENTS_PER_REPORT = 5
# 8 x 5 MB worst-case buffering. The Drive store runs at most 4 uploads at once (drive.py
# `_MAX_CONCURRENT_UPLOADS`), so up to 4 admitted uploads can wait for a Drive slot while holding
# their buffer; the overall deadline below bounds that wait.
MAX_INFLIGHT_UPLOADS = 8
READ_DEADLINE_SECONDS = 30.0
STORE_DEADLINE_SECONDS = 60.0  # whole `store.create`, queueing for the Drive slot included
_RETRYABLE = frozenset({DriveErrorCode.UNAVAILABLE, DriveErrorCode.RATE_LIMITED})


class BugReportAttachmentService:
    """Upload of one image to a report by its reporter (ADM-030, ADR-0023). HTTP-free.

    Two transactions: the checks run in tx 1, which is committed before the slow body read and
    Drive call (so no connection or row lock is held); the row and its audit entry are written in
    tx 2 after Drive accepted the file. The device cookie is resolved without rotation, so no
    error path has a cookie to carry."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        store: AttachmentStore,
        slots: asyncio.Semaphore,
        limiter: RateLimiter,
        user_limit: RateLimit,
        window_seconds: int,
        read_deadline_seconds: float = READ_DEADLINE_SECONDS,
    ) -> None:
        self.session = session
        self.store = store
        self.slots = slots
        self.limiter = limiter
        self.user_limit = user_limit
        self.window = timedelta(seconds=window_seconds)
        self.read_deadline_seconds = read_deadline_seconds
        self.repo = BugReportAttachmentRepository(session)
        self.audit = AuditLogger(session)

    async def upload(
        self,
        app: App,
        device: ResolvedDevice,
        account_id: uuid.UUID,
        report_id: uuid.UUID,
        *,
        content_type: str | None,
        content_length: str | None,
        chunks: AsyncIterator[bytes],
        ctx: AuditContext,
    ) -> BugReportAttachmentRead:
        user = await resolve_reporter(self.session, device, account_id)
        # Before any body is read, so rejected attempts count too.
        retry_after = self.limiter.hit("bug_report_attachment_user", str(user.id), self.user_limit)
        if retry_after is not None:
            raise RateLimitedError(retry_after)

        brief = await self.repo.report_brief(report_id)
        if brief is None or brief.user_id != user.id or brief.app_id != app.id:
            raise AttachmentUploadDeniedError
        if brief.status != BugReportStatus.OPEN.value:
            raise AttachmentUploadClosedError("report_done")
        if brief.now - brief.created_at >= self.window:
            raise AttachmentUploadClosedError("window_expired")
        if brief.attachment_count >= MAX_ATTACHMENTS_PER_REPORT:
            raise AttachmentLimitError(MAX_ATTACHMENTS_PER_REPORT)

        declared = image_types.parse_declared(content_type)
        if declared is None:
            raise UnsupportedMediaTypeError("Unsupported media type")
        if content_length is None or not content_length.isascii() or not content_length.isdigit():
            raise LengthRequiredError("Content-Length is required")
        length = int(content_length)
        if length > MAX_ATTACHMENT_BYTES:
            raise PayloadTooLargeError("Attachment is too large")
        if length < image_types.MIN_IMAGE_BYTES:
            raise UnsupportedMediaTypeError("Unsupported media type")

        await self.repo.commit()

        if self.slots.locked():
            raise AttachmentUploadBusyError
        await self.slots.acquire()  # no await between the check and the acquire
        try:
            data = await read_exact(
                chunks,
                expected=length,
                cap=MAX_ATTACHMENT_BYTES,
                deadline_s=self.read_deadline_seconds,
            )
            sniffed = image_types.sniff(data[: image_types.MIN_IMAGE_BYTES])
            if sniffed is None or sniffed != declared:
                raise UnsupportedMediaTypeError("Unsupported media type")
            attachment_id = uuid.uuid4()
            file_id = await self._create(app.key, report_id, attachment_id, sniffed, data)
        finally:
            self.slots.release()
        return await self._record(
            app, user, report_id, attachment_id, file_id, sniffed, len(data), ctx
        )

    async def _create(
        self,
        app_key: str,
        report_id: uuid.UUID,
        attachment_id: uuid.UUID,
        kind: ImageType,
        data: bytes,
    ) -> str:
        code: DriveErrorCode | None = None
        try:
            async with asyncio.timeout(STORE_DEADLINE_SECONDS):
                return await self.store.create(
                    app_key=app_key,
                    report_id=report_id,
                    attachment_id=attachment_id,
                    content_type=kind,
                    data=data,
                )
        except DriveError as exc:
            code = exc.code
        except TimeoutError:
            code = DriveErrorCode.UNAVAILABLE
        logger.warning(
            "bug_report_attachment_drive_failed report_id=%s attachment_id=%s code=%s",
            report_id,
            attachment_id,
            code.value,
        )
        raise AttachmentStorageUnavailableError(retryable=code in _RETRYABLE)

    async def _record(
        self,
        app: App,
        user: User,
        report_id: uuid.UUID,
        attachment_id: uuid.UUID,
        file_id: str,
        kind: ImageType,
        size: int,
        ctx: AuditContext,
    ) -> BugReportAttachmentRead:
        """Tx 2. Any failure deletes the Drive file again (best effort; the sweep is the net)."""
        try:
            locked = await self.repo.lock_report(report_id)
            if locked is None:
                raise AttachmentUploadDeniedError
            status, count = locked
            if status != BugReportStatus.OPEN.value:
                raise AttachmentUploadClosedError("report_done")
            if count >= MAX_ATTACHMENTS_PER_REPORT:
                raise AttachmentLimitError(MAX_ATTACHMENTS_PER_REPORT)
            row = await self.repo.add(
                BugReportAttachment(
                    id=attachment_id,
                    report_id=report_id,
                    drive_file_id=file_id,
                    content_type=kind.value,
                    size_bytes=size,
                )
            )
            # Ids and a count only: never the file id, content type, filename or content.
            await self.audit.record(
                AuditAction.BUG_REPORT_ATTACHMENT_ADDED,
                actor=EndUser(user.id),
                ctx=ctx,
                target=(AuditTarget.BUG_REPORT, report_id),
                app_key=app.key,
                org_id=None,
                changes={"attachment_id": str(attachment_id), "attachment_count": count + 1},
            )
            await self.repo.commit()
        except BaseException:
            await self.session.rollback()
            await self._discard(file_id, report_id, attachment_id)
            raise
        logger.info(
            "bug_report_attachment_added report_id=%s attachment_id=%s", report_id, attachment_id
        )
        return BugReportAttachmentRead(
            id=row.id, content_type=kind, size_bytes=row.size_bytes, created_at=row.created_at
        )

    async def _discard(self, file_id: str, report_id: uuid.UUID, attachment_id: uuid.UUID) -> None:
        failed = False
        try:
            await self.store.delete(file_id)
        except DriveError:
            failed = True
        if failed:
            logger.warning(
                "bug_report_attachment_cleanup_failed report_id=%s attachment_id=%s",
                report_id,
                attachment_id,
            )
