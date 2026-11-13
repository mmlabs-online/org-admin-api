"""`on_signup` hook: queue the verification email after the sign-up commits (USR-04)."""

import logging
import uuid
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RateLimitedError
from app.modules.auth.email_verification import DailyCapReachedError, EmailVerificationService
from app.modules.auth.throttle import EmailHasher
from app.modules.email.sender import OutgoingEmail
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)


class SignupHook(Protocol):
    """Runs in the sign-up transaction. Only record/enqueue work; no slow I/O."""

    async def __call__(self, user_id: uuid.UUID, session: AsyncSession) -> None: ...


class VerificationEmailHook:
    """Issues the verification token in the sign-up transaction; `schedule` must queue the
    message only after a successful commit (a FastAPI background task does)."""

    def __init__(
        self,
        *,
        link_origin: str,
        email_hasher: EmailHasher,
        daily_cap: int,
        schedule: Callable[[OutgoingEmail], None],
    ) -> None:
        self.link_origin = link_origin
        self.email_hasher = email_hasher
        self.daily_cap = daily_cap
        self.schedule = schedule

    async def __call__(self, user_id: uuid.UUID, session: AsyncSession) -> None:
        user = await UserRepository(session).get(user_id)
        if user is None:  # pragma: no cover - created in the same transaction
            return
        service = EmailVerificationService(
            session, email_hasher=self.email_hasher, daily_cap=self.daily_cap
        )
        try:
            email = await service.prepare(user, self.link_origin)
        except RateLimitedError as exc:  # sign-up still succeeds; the user can resend later
            reason = exc.reason if isinstance(exc, DailyCapReachedError) else "limited"
            logger.warning("email_not_sent reason=%s purpose=verify user_id=%s", reason, user_id)
            return
        self.schedule(email)
