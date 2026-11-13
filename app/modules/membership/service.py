"""Join an app (D8-D9): "Continue as <account>" is the consent; `open` assigns the default role."""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.recorder import AuditRecorder
from app.modules.auth.errors import OriginNotAllowedError
from app.modules.membership.errors import (
    AppMismatchError,
    JoinApprovalUnavailableError,
    JoinClosedError,
    JoinUnavailableError,
)
from app.modules.membership.schemas import JoinRequest
from app.modules.rbac.authz import AuthzService
from app.modules.rbac.models import ADMIN_APP_KEY, App, AppStatus, JoinPolicy
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import AssignmentService
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import ResolvedDevice
from app.modules.tokens.errors import EmailUnverifiedError
from app.modules.users.models import UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JoinResult:
    created: bool  # False: already a member, nothing written


class JoinService:
    def __init__(self, session: AsyncSession, recorder: AuditRecorder) -> None:
        self.rbac = RbacRepository(session)
        self.authz = AuthzService(session)
        self.assignments = AssignmentService(session)
        self.accounts = SessionRepository(session)
        self.users = UserRepository(session)
        self.recorder = recorder

    async def join(
        self, calling_app: App, device: ResolvedDevice, key: str, data: JoinRequest
    ) -> JoinResult:
        if key != calling_app.key:  # only an app can join users into itself
            raise AppMismatchError("App does not match the calling app")
        if key == ADMIN_APP_KEY:  # before any policy read
            raise JoinClosedError("This app does not accept joins")
        # Lock first (ADR-0003 §10, order app -> users): the default role is read under it.
        app = await self.rbac.lock_app(key)
        if app is None or app.status != AppStatus.ACTIVE.value:
            raise OriginNotAllowedError("Origin is not allowed")

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

        # Membership = any active app-scope role, the same test `/token` uses.
        if await self.authz.effective_roles(user.id, app.key):
            return JoinResult(created=False)

        if app.join_policy == JoinPolicy.CLOSED.value:
            raise JoinClosedError("This app does not accept joins")
        if app.join_policy == JoinPolicy.APPROVAL.value:
            raise JoinApprovalUnavailableError("Joining this app needs approval (not available)")

        role = (
            None
            if app.default_role_id is None
            else await self.rbac.get_role(app.id, app.default_role_id)
        )
        if role is None:
            raise JoinUnavailableError("This app cannot be joined right now")
        if await self.rbac.role_has_admin_permission(role.id):
            logger.warning("join_default_role_admin app_id=%s role_id=%s", app.id, role.id)
            raise JoinUnavailableError("This app cannot be joined right now")

        ra, _ = await self.assignments._grant_locked(app, role, user.id, None, None, None)
        await self.recorder.record(
            "app.join",
            actor_user_id=user.id,
            app_id=app.id,
            role_id=role.id,
            assignment_id=ra.id,
        )
        logger.info("app_joined user_id=%s app=%s role_id=%s", user.id, app.key, role.id)
        return JoinResult(created=True)
