"""Who is calling an admin endpoint (ADR-0008): reload the user and its admin permissions."""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.modules.rbac.authz import AuthzService
from app.modules.rbac.models import ADMIN_APP_KEY
from app.modules.users.models import UserStatus
from app.modules.users.repository import UserRepository


@dataclass(frozen=True)
class AdminPrincipal:
    user_id: uuid.UUID
    permissions: frozenset[str]
    token_id: str


class AdminAuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.users = UserRepository(session)
        self.authz = AuthzService(session)

    async def principal(self, user_id: uuid.UUID, token_id: str) -> AdminPrincipal:
        """Per request, so disable, unverify, revoke and expiry apply on the next call."""
        user = await self.users.get(user_id)
        if user is None or user.status != UserStatus.ACTIVE.value or user.email_verified_at is None:
            raise UnauthorizedError("Authentication required")
        permissions = await self.authz.effective_permissions(user.id, ADMIN_APP_KEY)
        return AdminPrincipal(user.id, frozenset(permissions), token_id)
