import ipaddress
import logging
import math
import re
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RateLimitedError
from app.modules.auth.errors import (
    AppHeaderRequiredError,
    EmailTakenError,
    InvalidCredentialsError,
    OriginNotAllowedError,
    TurnstileFailedError,
    TurnstileRequiredError,
)
from app.modules.auth.hooks import SignupHook
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
from app.modules.auth.repository import FailureState, LoginFailureRepository
from app.modules.auth.schemas import LoginRequest, LogoutRequest, SignupRequest
from app.modules.auth.throttle import TURNSTILE_AFTER, EmailHasher, backoff_minutes
from app.modules.auth.turnstile import TurnstileVerifier
from app.modules.rbac.models import APP_KEY_RE, App, AppStatus
from app.modules.rbac.origins import allowed_origins, normalize_origin
from app.modules.rbac.repository import RbacRepository
from app.modules.sessions.service import DeviceSessionService, ResolvedDevice
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)


def normalize_email(email: str) -> str:
    return email.strip().lower()


class SignupService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: PasswordPolicy,
        hasher: PasswordHasher,
        verifier: TurnstileVerifier,
        hook: SignupHook,
    ) -> None:
        self.session = session
        self.repo = UserRepository(session)
        self.policy = policy
        self.hasher = hasher
        self.verifier = verifier
        self.hook = hook

    async def signup(self, data: SignupRequest, remote_ip: str) -> User:
        email = normalize_email(str(data.email))
        password = data.password.get_secret_value()
        self.policy.check(password)
        if not await self.verifier.verify(data.turnstile_token, remote_ip):
            raise TurnstileFailedError("Verification failed")
        # Hash before touching the DB so 201 and 409 take similar time.
        password_hash = await self.hasher.hash(password)
        try:
            user = await self.repo.add(User(email=email, password_hash=password_hash))
        except IntegrityError as exc:  # unique email (case-insensitive via citext)
            raise EmailTakenError("Email is already registered") from exc
        await self.hook(user.id, self.session)
        return user


class AppContextService:
    """CSRF check for endpoints that read or set `__Host-mm_device` (D12, ADR-0004)."""

    def __init__(self, session: AsyncSession, *, app_env: str) -> None:
        self.repo = RbacRepository(session)
        self.app_env = app_env

    async def check(self, app_key: str | None, origin: str | None) -> App:
        if app_key is None or re.fullmatch(APP_KEY_RE, app_key) is None:
            raise AppHeaderRequiredError("X-MM-App header is required")
        app = await self.repo.get_app_by_key(app_key)
        if app is None or app.status != AppStatus.ACTIVE.value:
            raise OriginNotAllowedError("Origin is not allowed")
        if origin is None or origin == "null":
            raise OriginNotAllowedError("Origin is not allowed")
        try:
            normalized = normalize_origin(origin)
        except ValueError:
            raise OriginNotAllowedError("Origin is not allowed") from None
        if normalized not in allowed_origins(app.origins, self.app_env):
            raise OriginNotAllowedError("Origin is not allowed")
        return app


@dataclass(frozen=True)
class LoginResult:
    account_id: uuid.UUID
    user: User
    new_cookie: str | None


class LoginService:
    """D11 order: per-email backoff, Turnstile after 3 failures, password check."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        hasher: PasswordHasher,
        verifier: TurnstileVerifier,
        email_hasher: EmailHasher,
        devices: DeviceSessionService,
    ) -> None:
        self.users = UserRepository(session)
        self.failures = LoginFailureRepository(session)
        self.hasher = hasher
        self.verifier = verifier
        self.email_hasher = email_hasher
        self.devices = devices

    @staticmethod
    def _raise_if_blocked(state: FailureState) -> None:
        if state.blocked_until is not None and state.blocked_until > state.now:
            retry_after = math.ceil((state.blocked_until - state.now).total_seconds())
            raise RateLimitedError(max(1, retry_after))

    async def login(
        self,
        data: LoginRequest,
        *,
        cookies: list[str],
        remote_ip: str,
        user_agent: str | None,
    ) -> LoginResult:
        email = normalize_email(str(data.email))
        email_hmac = self.email_hasher.hmac(email)
        # Phase 1 (no lock): refuse early; the Turnstile call stays outside any lock.
        state = await self.failures.get(email_hmac)
        self._raise_if_blocked(state)
        turnstile_ok = False
        if state.failures >= TURNSTILE_AFTER:
            if data.turnstile_token is None:
                raise TurnstileRequiredError("Verification is required")
            if not await self.verifier.verify(data.turnstile_token, remote_ip):
                raise TurnstileFailedError("Verification failed")
            turnstile_ok = True

        # Phase 2 (S1): reserve the attempt atomically, then commit before Argon2 so the row
        # lock is released and the attempt stays counted whatever the outcome.
        failures = await self.failures.reserve(email_hmac, turnstile_ok=turnstile_ok)
        if failures is None:  # a concurrent attempt reached a threshold first
            self._raise_if_blocked(await self.failures.get(email_hmac))
            raise TurnstileRequiredError("Verification is required")
        minutes = backoff_minutes(failures)
        if minutes is not None:
            await self.failures.set_block(email_hmac, minutes)
        await self.failures.commit()

        password = data.password.get_secret_value()
        user = await self.users.get_by_email_for_share(email)
        if user is None:
            await self.hasher.verify_dummy(password)
            ok = False
        else:
            # A disabled user's password is still checked; the outcome is the same 401 (A6).
            ok = await self.hasher.verify(user.password_hash, password)
            ok = ok and user.status == UserStatus.ACTIVE.value
        if user is None or not ok:
            logger.info("login_failed")  # already counted by the reservation
            raise InvalidCredentialsError("Invalid email or password")

        await self.failures.delete(email_hmac)
        device: ResolvedDevice = await self.devices.start_for_login(
            cookies, ip=_valid_ip(remote_ip), user_agent=user_agent
        )
        account_id = await self.devices.add_account(device.device_id, user.id)
        logger.info("login_succeeded account_session_id=%s", account_id)
        return LoginResult(account_id, user, device.new_cookie)


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


class LogoutService:
    def __init__(self, devices: DeviceSessionService) -> None:
        self.devices = devices

    async def logout(self, data: LogoutRequest, device: ResolvedDevice) -> None:
        """`{account_id}` revokes that account session on this device only (no oracle).

        With the previous cookie value, only account sessions created before the rotation
        are reachable (N1); `{all}` still revokes the whole device.
        """
        if data.all:
            await self.devices.revoke_device(device.device_id)
        elif data.account_id is not None:
            await self.devices.revoke_account(
                device.device_id, data.account_id, created_before=device.accounts_created_before
            )
