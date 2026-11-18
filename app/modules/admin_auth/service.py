"""Who is calling an admin endpoint (ADR-0008): reload the user and its admin permissions."""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.modules.rbac.authz import AuthzService
from app.modules.rbac.models import ADMIN_APP_KEY
from app.modules.sessions.repository import SessionRepository
from app.modules.users.models import UserStatus
from app.modules.users.repository import UserRepository


class AdminSessionEndedError(UnauthorizedError):
    """The account session the admin token is bound to is revoked or unknown (USR-14)."""


@dataclass(frozen=True)
class AdminPrincipal:
    user_id: uuid.UUID
    permissions: frozenset[str]
    token_id: str


@dataclass(frozen=True)
class AdminSelfView:
    user_id: uuid.UUID
    roles: list[str]
    permissions: list[str]


class AdminAuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.users = UserRepository(session)
        self.sessions = SessionRepository(session)
        self.authz = AuthzService(session)

    async def principal(
        self, user_id: uuid.UUID, token_id: str, *, session_id: uuid.UUID
    ) -> AdminPrincipal:
        """Per request, so disable, unverify, revoke, expiry and the end of the token's account
        session (logout, reset, staff revoke) apply on the next call."""
        user = await self.users.get(user_id)
        if user is None or user.status != UserStatus.ACTIVE.value or user.email_verified_at is None:
            raise UnauthorizedError("Authentication required")
        # Fail closed: no fallback, a lookup error is a 500, never an allow.
        if not await self.sessions.is_account_active(session_id, user.id):
            raise AdminSessionEndedError("Authentication required")
        permissions = await self.authz.effective_permissions(user.id, ADMIN_APP_KEY)
        return AdminPrincipal(user.id, frozenset(permissions), token_id)

    async def self_view(self, principal: AdminPrincipal) -> AdminSelfView:
        """The caller's own roles (app scope, like enforcement) and this request's permissions."""
        roles = await self.authz.effective_roles(principal.user_id, ADMIN_APP_KEY)
        return AdminSelfView(principal.user_id, roles, sorted(principal.permissions))
