"""Email verification: issue a one-time link, verify it, resend (USR-04, ADR-0007)."""

import hashlib
import logging
import secrets
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RateLimitedError
from app.modules.auth.email_throttle import (
    DAILY_CAP_RETRY_SECONDS,
    cap_refusal,
    resend_retry_after,
)
from app.modules.auth.errors import EmailAlreadyVerifiedError, TokenInvalidError
from app.modules.auth.repository import EmailSendLogRepository, EmailTokenRepository
from app.modules.auth.throttle import EmailHasher
from app.modules.email.messages import verification_message
from app.modules.email.sender import OutgoingEmail
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import ResolvedDevice
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)

VERIFY = "verify"
VERIFY_TTL = timedelta(hours=24)
VERIFY_PATH = "/verify-email"  # fixed page path on every app (docs/consumers.md)


def email_token_hash(raw: str) -> bytes:
    """A fast hash is enough for a 256-bit random token."""
    return hashlib.sha256(raw.encode("utf-8")).digest()


class DailyCapReachedError(RateLimitedError):
    """The global daily send cap protecting the shared Gmail quota, or the purpose's share of
    it, is reached; `reason` is `daily_cap` or `purpose_cap` (logs only)."""

    def __init__(self, retry_after: int, reason: str) -> None:
        super().__init__(retry_after)
        self.reason = reason


async def daily_cap_refusal(
    tokens: EmailTokenRepository, sends: EmailSendLogRepository, purpose: str, daily_cap: int
) -> str | None:
    """None when one more `purpose` email fits the global daily cap, else the refusal reason
    (`daily_cap` or `purpose_cap`; USR-04b, USR-04d).

    Counts real sends (tokens issued in the last 24 h) under one global advisory lock, so
    concurrent requests cannot all pass the check.
    """
    await sends.lock_global()
    total_24h, verify_24h = await tokens.count_issued_24h()
    return cap_refusal(purpose, total_24h=total_24h, verify_24h=verify_24h, daily_cap=daily_cap)


class EmailVerificationService:
    def __init__(self, session: AsyncSession, *, email_hasher: EmailHasher, daily_cap: int) -> None:
        self.tokens = EmailTokenRepository(session)
        self.sends = EmailSendLogRepository(session)
        self.users = UserRepository(session)
        self.accounts = SessionRepository(session)
        self.email_hasher = email_hasher
        self.daily_cap = daily_cap

    async def prepare(self, user: User, link_origin: str) -> OutgoingEmail:
        """Count the send and issue a new token; the message to queue after commit.

        Raises `RateLimitedError` (per email) or `DailyCapReachedError` (global) when limited.
        """
        # Same normalization as sign-up and login (`auth.service.normalize_email`).
        email_hmac = self.email_hasher.hmac(user.email.strip().lower())
        await self.sends.lock(email_hmac, VERIFY)
        window = await self.sends.window(email_hmac, VERIFY)
        retry_after = resend_retry_after(window.now, window.last, window.count, window.oldest)
        if retry_after is not None:
            raise RateLimitedError(retry_after)
        reason = await daily_cap_refusal(self.tokens, self.sends, VERIFY, self.daily_cap)
        if reason is not None:
            raise DailyCapReachedError(DAILY_CAP_RETRY_SECONDS, reason)
        await self.sends.insert(email_hmac, VERIFY)

        token = secrets.token_urlsafe(32)
        await self.tokens.issue(user.id, VERIFY, email_token_hash(token), VERIFY_TTL)
        subject, body = verification_message(f"{link_origin}{VERIFY_PATH}#token={token}")
        return OutgoingEmail(
            to=user.email, subject=subject, body=body, purpose=VERIFY, user_id=user.id
        )

    async def verify(self, token: str) -> None:
        """Consume the token and mark the email verified; idempotent once verified."""
        token_hash = email_token_hash(token)
        user_id = await self.tokens.find_user_id(token_hash, VERIFY)
        if user_id is None:
            raise TokenInvalidError("Token is invalid or expired")
        user = await self.users.get_for_update(user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise TokenInvalidError("Token is invalid or expired")
        if await self.tokens.consume(token_hash, VERIFY) is None:
            if user.email_verified_at is not None:  # double click, mail scanner
                return
            raise TokenInvalidError("Token is invalid or expired")
        if user.email_verified_at is None:
            await self.users.mark_email_verified(user)
        await self.tokens.invalidate(user.id, VERIFY)
        logger.info("email_verified user_id=%s", user.id)

    async def resend(
        self, device: ResolvedDevice, account_id: uuid.UUID, link_origin: str
    ) -> OutgoingEmail:
        account = await self.accounts.get_active_account(
            device.device_id, account_id, created_before=device.accounts_created_before
        )
        if account is None:
            raise SessionInvalidError("Session is invalid")
        user = await self.users.get(account.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise SessionInvalidError("Session is invalid")
        if user.email_verified_at is not None:
            raise EmailAlreadyVerifiedError("Email address is already verified")
        try:
            return await self.prepare(user, link_origin)
        except DailyCapReachedError as exc:
            logger.warning(
                "email_not_sent reason=%s purpose=verify user_id=%s", exc.reason, user.id
            )
            raise
