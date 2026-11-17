"""Per-app access tokens (D7, ADR-0005): checks in the order of the USR-03c plan, then sign."""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jwt_keys import KeySet
from app.modules.rbac.authz import AuthzService
from app.modules.rbac.models import App, JoinPolicy, OrgMode
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import ResolvedDevice
from app.modules.tokens.errors import (
    AudienceMismatchError,
    EmailUnverifiedError,
    NotMemberError,
    OrgAccessDeniedError,
    OrgNotSupportedError,
)
from app.modules.tokens.schemas import TokenRequest
from app.modules.users.models import UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    expires_in: int


class TokenService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        key_set: KeySet,
        issuer: str,
        ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.accounts = SessionRepository(session)
        self.users = UserRepository(session)
        self.authz = AuthzService(session)
        self.key_set = key_set
        self.issuer = issuer
        self.ttl = ttl_seconds
        self.clock = clock

    async def issue(self, app: App, device: ResolvedDevice, data: TokenRequest) -> IssuedToken:
        if data.audience != app.key:
            raise AudienceMismatchError("Audience does not match the calling app")
        if data.org_id is not None and app.org_mode == OrgMode.NONE.value:
            raise OrgNotSupportedError(
                "This app does not use organizations",
                details=[{"field": "org_id", "reason": "not_supported"}],
            )
        # No `headers`: the cookie is not cleared, other accounts on the device stay signed in.
        account = await self.accounts.get_active_account(
            device.device_id, data.account_id, created_before=device.accounts_created_before
        )
        if account is None:
            raise SessionInvalidError("Session is invalid")
        user = await self.users.get(account.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise SessionInvalidError("Session is invalid")
        if user.email_verified_at is None:
            raise EmailUnverifiedError("Email address is not verified")
        roles = await self.authz.effective_roles(user.id, app.key)
        if not roles:
            # What join would do now; USR-03g makes `approval` true again.
            join_available = (
                app.join_policy == JoinPolicy.OPEN.value and app.default_role_id is not None
            )
            raise NotMemberError(
                "Not a member of this app", details={"join_available": join_available}
            )

        claims: dict[str, object] = {}
        if data.org_id is not None:
            # Org-exact: the app-scope role above must not make an unknown, suspended or
            # foreign org look usable. One answer for all three (no existence oracle).
            org_roles = await self.authz.org_role_keys(user.id, app.key, data.org_id)
            if not org_roles:
                raise OrgAccessDeniedError("No access to this organization", details={})
            roles = await self.authz.effective_roles(user.id, app.key, data.org_id)
            claims["org"] = str(data.org_id)
            claims["org_roles"] = org_roles  # org-exact only (ADR-0015); `roles` stays the union

        now = int(self.clock())
        jti = str(uuid.uuid4())
        token = self.key_set.sign(
            {
                "iss": self.issuer,
                "aud": app.key,
                "sub": str(user.id),
                "email": user.email,
                "email_verified": True,  # unverified users were refused above
                "roles": roles,
                **claims,
                "iat": now,
                "exp": now + self.ttl,
                "jti": jti,
            }
        )
        logger.info(
            "token_issued user_id=%s account_session_id=%s aud=%s org_id=%s kid=%s jti=%s",
            user.id,
            account.id,
            app.key,
            data.org_id,
            self.key_set.signing_kid,
            jti,
        )
        return IssuedToken(access_token=token, expires_in=self.ttl)
