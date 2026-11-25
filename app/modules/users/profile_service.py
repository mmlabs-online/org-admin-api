"""Self-service display name (USR-33, D40). Cookie-class caller (R3 class 2)."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RateLimitedError
from app.core.rate_limit import RateLimit, RateLimiter
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.service import AuditLogger
from app.modules.rbac.models import App
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import ResolvedDevice
from app.modules.tokens.errors import EmailUnverifiedError
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)


class DisplayNameService:
    def __init__(self, session: AsyncSession, *, limiter: RateLimiter, user_limit: RateLimit):
        self.session = session
        self.limiter = limiter
        self.user_limit = user_limit
        self.repo = UserRepository(session)
        self.audit = AuditLogger(session)

    async def set(
        self,
        app: App,
        device: ResolvedDevice,
        account_id: uuid.UUID,
        value: str | None,
        *,
        ctx: AuditContext,
    ) -> User:
        """The user behind `account_id` on this device sets or clears their own name. The row is
        locked `FOR NO KEY UPDATE` (we write; a `FOR SHARE` upgrade would deadlock two saves)."""
        account = await SessionRepository(self.session).get_active_account(
            device.device_id, account_id, created_before=device.accounts_created_before
        )
        if account is None:
            raise SessionInvalidError("Session is invalid")
        user = await self.repo.get_for_update(account.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise SessionInvalidError("Session is invalid")
        if user.email_verified_at is None:
            raise EmailUnverifiedError("Email address is not verified")
        retry_after = self.limiter.hit("profile_user", str(user.id), self.user_limit)
        if retry_after is not None:
            raise RateLimitedError(retry_after)
        if user.display_name == value:
            return user  # no write: updated_at stays unchanged
        await self.repo.set_display_name(user, value)
        await self.audit.record(
            AuditAction.USER_DISPLAY_NAME_CHANGED,
            actor=EndUser(user.id),
            ctx=ctx,
            target=(AuditTarget.USER, user.id),
            app_key=app.key,
            changes={"display_name": {"set": value is not None}},
        )
        logger.info("display_name_changed user_id=%s cleared=%s via=self", user.id, value is None)
        return user
