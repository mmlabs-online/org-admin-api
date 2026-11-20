"""Bug-report submit (ADM-028, ADR-0019). Cookie-class caller (R3 class 2)."""

import logging
import math
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RateLimitedError
from app.core.rate_limit import RateLimit, RateLimiter
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.service import AuditLogger
from app.modules.bug_reports.errors import BugReportDailyLimitError, BugReportOpenLimitError
from app.modules.bug_reports.models import BugReport, BugReportStatus
from app.modules.bug_reports.repository import BugReportRepository
from app.modules.bug_reports.schemas import BugReportCreate, BugReportCreated
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
        self.accounts = SessionRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditLogger(session)

    async def _reporter(self, device: ResolvedDevice, account_id: uuid.UUID) -> User:
        # No `headers`: the cookie is not cleared, other accounts on the device stay signed in.
        account = await self.accounts.get_active_account(
            device.device_id, account_id, created_before=device.accounts_created_before
        )
        if account is None:
            raise SessionInvalidError("Session is invalid")
        user = await self.users.get_for_share(account.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise SessionInvalidError("Session is invalid")
        if user.email_verified_at is None:
            raise EmailUnverifiedError("Email address is not verified")
        return user

    async def submit(
        self, app: App, device: ResolvedDevice, data: BugReportCreate, *, ctx: AuditContext
    ) -> BugReportCreated:
        user = await self._reporter(device, data.account_id)
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
