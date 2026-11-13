"""Password forgot/reset by a one-time email link (USR-04b, ADR-0007)."""

import logging
import secrets
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.email_throttle import resend_retry_after
from app.modules.auth.email_verification import VERIFY, daily_cap_allows, email_token_hash
from app.modules.auth.errors import TokenInvalidError, TurnstileFailedError
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
from app.modules.auth.repository import (
    EmailSendLogRepository,
    EmailTokenRepository,
    LoginFailureRepository,
)
from app.modules.auth.schemas import ForgotPasswordRequest
from app.modules.auth.service import normalize_email
from app.modules.auth.throttle import EmailHasher
from app.modules.auth.turnstile import TurnstileVerifier
from app.modules.email.messages import reset_message
from app.modules.email.sender import OutgoingEmail
from app.modules.sessions.repository import SessionRepository
from app.modules.users.models import UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)

RESET = "reset"
RESET_TTL = timedelta(minutes=30)
RESET_PATH = "/reset-password"  # fixed page path on every app (docs/consumers.md)


class PasswordResetService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        email_hasher: EmailHasher,
        daily_cap: int,
        verifier: TurnstileVerifier,
        policy: PasswordPolicy,
        hasher: PasswordHasher,
    ) -> None:
        self.tokens = EmailTokenRepository(session)
        self.sends = EmailSendLogRepository(session)
        self.users = UserRepository(session)
        self.sessions = SessionRepository(session)
        self.failures = LoginFailureRepository(session)
        self.email_hasher = email_hasher
        self.daily_cap = daily_cap
        self.verifier = verifier
        self.policy = policy
        self.hasher = hasher

    async def forgot(
        self, data: ForgotPasswordRequest, remote_ip: str, link_origin: str
    ) -> OutgoingEmail | None:
        """The reset email to queue after commit, or None. Never reveals whether the email is
        registered: limits are silent and every accepted request writes one send-log row."""
        if not await self.verifier.verify(data.turnstile_token, remote_ip):
            raise TurnstileFailedError("Verification failed")
        email = normalize_email(str(data.email))
        email_hmac = self.email_hasher.hmac(email)
        await self.sends.lock(email_hmac, RESET)
        window = await self.sends.window(email_hmac, RESET)
        if resend_retry_after(window.now, window.last, window.count, window.oldest) is not None:
            return None
        if not await daily_cap_allows(self.tokens, self.sends, RESET, self.daily_cap):
            logger.warning("email_not_sent reason=daily_cap purpose=reset")
            return None
        await self.sends.insert(email_hmac, RESET)

        user = await self.users.get_by_email_for_share(email)
        if user is None or user.status != UserStatus.ACTIVE.value:
            return None
        token = secrets.token_urlsafe(32)
        await self.tokens.issue(user.id, RESET, email_token_hash(token), RESET_TTL)
        subject, body = reset_message(f"{link_origin}{RESET_PATH}#token={token}")
        logger.info("password_reset_requested user_id=%s", user.id)
        return OutgoingEmail(
            to=user.email, subject=subject, body=body, purpose=RESET, user_id=user.id
        )

    async def reset(self, token: str, new_password: str) -> None:
        """Set the new password, end every account session of the user, consume the token.

        A weak password keeps the token usable; a disabled user's token is not consumed
        (the request transaction rolls back on the raised error).
        """
        token_hash = email_token_hash(token)
        user_id = await self.tokens.find_active_user_id(token_hash, RESET)
        if user_id is None:
            raise TokenInvalidError("Token is invalid or expired")
        self.policy.check(new_password)
        password_hash = await self.hasher.hash(new_password)  # off the loop, before row locks

        user = await self.users.get_for_update(user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise TokenInvalidError("Token is invalid or expired")
        if await self.tokens.consume(token_hash, RESET) is None:  # a concurrent reset won
            raise TokenInvalidError("Token is invalid or expired")
        await self.users.set_password_hash(user, password_hash)
        await self.sessions.revoke_all_accounts_of_user(user.id)
        await self.tokens.invalidate(user.id, RESET)
        await self.tokens.invalidate(user.id, VERIFY)
        await self.failures.delete(self.email_hasher.hmac(normalize_email(user.email)))
        if user.email_verified_at is None:
            await self.users.mark_email_verified(user)
        logger.info("password_reset user_id=%s", user.id)
