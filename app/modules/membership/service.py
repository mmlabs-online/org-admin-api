"""Join an app (D8-D9): "Continue as <account>" is the consent; `open` assigns the default role."""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.diff import change
from app.modules.audit.service import AuditLogger
from app.modules.auth.errors import OriginNotAllowedError
from app.modules.membership.errors import (
    AppMismatchError,
    JoinApprovalUnavailableError,
    JoinClosedError,
    JoinUnavailableError,
)
from app.modules.membership.schemas import JoinRequest
from app.modules.rbac.actor import SYSTEM_JOIN
from app.modules.rbac.authz import AuthzService
from app.modules.rbac.models import ADMIN_APP_KEY, App, AppStatus, JoinPolicy, Role
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


async def lock_joinable_app(rbac: RbacRepository, key: str) -> App:
    """Lock an active, joinable app (ADR-0003 §10: app first, then users). Shared with the CLI."""
    if key == ADMIN_APP_KEY:  # before any policy read
        raise JoinClosedError("This app does not accept joins")
    app = await rbac.lock_app(key)
    if app is None or app.status != AppStatus.ACTIVE.value:
        raise OriginNotAllowedError("Origin is not allowed")
    return app


async def join_default_role(rbac: RbacRepository, app: App) -> Role:
    """The default role `open` joins assign; refuses closed/approval/missing/admin roles."""
    if app.join_policy == JoinPolicy.CLOSED.value:
        raise JoinClosedError("This app does not accept joins")
    if app.join_policy == JoinPolicy.APPROVAL.value:
        raise JoinApprovalUnavailableError("Joining this app needs approval (not available)")

    role = None if app.default_role_id is None else await rbac.get_role(app.id, app.default_role_id)
    if role is None:
        raise JoinUnavailableError("This app cannot be joined right now")
    if await rbac.role_has_admin_permission(role.id):
        logger.warning("join_default_role_admin app_id=%s role_id=%s", app.id, role.id)
        raise JoinUnavailableError("This app cannot be joined right now")
    return role


class JoinService:
    def __init__(self, session: AsyncSession) -> None:
        self.rbac = RbacRepository(session)
        self.authz = AuthzService(session)
        self.assignments = AssignmentService(session)
        self.accounts = SessionRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditLogger(session)

    async def join(
        self,
        calling_app: App,
        device: ResolvedDevice,
        key: str,
        data: JoinRequest,
        *,
        ctx: AuditContext,
    ) -> JoinResult:
        if key != calling_app.key:  # only an app can join users into itself
            raise AppMismatchError("App does not match the calling app")
        app = await lock_joinable_app(self.rbac, key)

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

        role = await join_default_role(self.rbac, app)

        ra, _ = await self.assignments._grant_locked(
            app, role, user.id, None, None, actor=SYSTEM_JOIN
        )
        await self.audit.record(
            AuditAction.APP_JOINED,
            actor=EndUser(user.id),
            ctx=ctx,
            target=(AuditTarget.ROLE_ASSIGNMENT, ra.id),
            app_key=app.key,
            changes={"role_key": change(None, role.key)},
        )
        logger.info("app_joined user_id=%s app=%s role_id=%s", user.id, app.key, role.id)
        return JoinResult(created=True)
