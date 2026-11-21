"""Org member removal (USR-30, ADR-0026): a member holding `<app>:org:remove-member` in the org
removes another person's org roles of the calling app in that org.

Cookie-class caller (R3 class 2). The permission is read org-exact. Refusals write no audit row
(end users, like invitations) and log reasons without emails.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RateLimitedError
from app.core.rate_limit import RateLimit, RateLimiter
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.service import AuditLogger
from app.modules.auth.errors import OriginNotAllowedError
from app.modules.auth.service import normalize_email
from app.modules.auth.throttle import EmailHasher
from app.modules.invitations.repository import InvitationRepository
from app.modules.organizations.auth_service import _org_not_supported
from app.modules.organizations.errors import (
    LastOrgOwnerError,
    MemberNotFoundError,
    MemberOutranksCallerError,
    OrgMemberRemoveUnavailableError,
    OrgRemoveMemberForbiddenError,
    SelfRemovalForbiddenError,
)
from app.modules.organizations.member_rules import leaves_no_owner, outranks
from app.modules.rbac.authz import AuthzService, permits
from app.modules.rbac.models import App, AppStatus, OrgMode
from app.modules.rbac.repository import RbacRepository
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import ResolvedDevice
from app.modules.tokens.errors import EmailUnverifiedError, OrgAccessDeniedError
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)

REMOVE_ACTION = "org:remove-member"  # permission `<app>:org:remove-member`, checked org-exact


class OrgMemberService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        email_hasher: EmailHasher,
        limiter: RateLimiter,
        user_limit: RateLimit,
    ) -> None:
        self.rbac = RbacRepository(session)
        self.authz = AuthzService(session)
        self.accounts = SessionRepository(session)
        self.users = UserRepository(session)
        self.invitations = InvitationRepository(session)
        self.audit = AuditLogger(session)
        self.email_hasher = email_hasher
        self.limiter = limiter
        self.user_limit = user_limit

    async def _user(self, device: ResolvedDevice, account_id: uuid.UUID) -> User:
        # No `headers`: the cookie is not cleared, other accounts on the device stay signed in.
        account = await self.accounts.get_active_account(
            device.device_id, account_id, created_before=device.accounts_created_before
        )
        if account is None:
            raise SessionInvalidError("Session is invalid")
        user = await self.users.get(account.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise SessionInvalidError("Session is invalid")
        if user.email_verified_at is None:
            raise EmailUnverifiedError("Email address is not verified")
        return user

    @staticmethod
    def _refuse(reason: str, org_id: uuid.UUID, app: App) -> None:
        logger.warning(
            "org_member_remove_denied reason=%s org_id=%s app=%s", reason, org_id, app.key
        )

    async def remove(
        self,
        calling_app: App,
        device: ResolvedDevice,
        org_id: uuid.UUID,
        target_user_id: uuid.UUID,
        account_id: uuid.UUID,
        *,
        ctx: AuditContext,
    ) -> None:
        # `lock_app` is the first statement and the only lock: every role_assignments writer
        # takes it (ADR-0003 §10), so two owners removing each other queue here and the second
        # one recounts the owners after the first has committed.
        app = await self.rbac.lock_app(calling_app.key)
        if app is None or app.status != AppStatus.ACTIVE.value:
            raise OriginNotAllowedError("Origin is not allowed")
        if app.org_mode != OrgMode.REQUIRED.value:
            raise _org_not_supported()
        user = await self._user(device, account_id)

        retry_after = self.limiter.hit("org_member_remove_user", str(user.id), self.user_limit)
        if retry_after is not None:
            raise RateLimitedError(retry_after)

        # Org-exact: one answer for an unknown, suspended or not-a-member org (no oracle).
        if not await self.authz.org_role_keys(user.id, app.key, org_id):
            raise OrgAccessDeniedError("No access to this organization", details={})
        caller_permissions = await self.authz.org_permissions(user.id, app.key, org_id)
        if not permits(caller_permissions, f"{app.key}:{REMOVE_ACTION}"):
            self._refuse("no_permission", org_id, app)
            raise OrgRemoveMemberForbiddenError(
                "Not allowed to remove members of this organization"
            )

        owner = (
            None
            if app.org_owner_role_id is None
            else await self.rbac.get_role(app.id, app.org_owner_role_id)
        )
        if owner is None:
            logger.warning("org_member_remove_unavailable app_id=%s", app.id)
            raise OrgMemberRemoveUnavailableError("Member removal is not available right now")

        if target_user_id == user.id:
            self._refuse("self", org_id, app)
            raise SelfRemovalForbiddenError("You cannot remove yourself")

        roles = await self.rbac.org_roles_held(target_user_id, app.id, org_id)
        if not roles:
            self._refuse("not_found", org_id, app)
            raise MemberNotFoundError("Member not found")

        target_permissions = await self.authz.org_permissions(target_user_id, app.key, org_id)
        has_admin = False
        for role in roles:
            has_admin = has_admin or await self.rbac.role_has_admin_permission(role.id)
        if outranks(target_permissions, has_admin, caller_permissions):
            self._refuse("outranks", org_id, app)
            raise MemberOutranksCallerError("This member cannot be removed by you")

        was_owner = any(role.id == owner.id for role in roles)
        other_owners = await self.rbac.count_other_org_owners(owner.id, org_id, target_user_id)
        if leaves_no_owner(was_owner, other_owners):
            self._refuse("last_owner", org_id, app)
            raise LastOrgOwnerError("The last owner of an organization cannot be removed")

        role_keys = await self.rbac.delete_org_assignments(target_user_id, app.id, org_id)
        actor = EndUser(user.id)

        # The removed person must not rejoin through an invitation that is still pending.
        target = await self.users.get(target_user_id)
        if target is not None:
            email_hmac = self.email_hasher.hmac(normalize_email(target.email))
            for invitation_id in await self.invitations.supersede_pending(
                org_id, app.id, email_hmac
            ):
                await self.audit.record(
                    AuditAction.ORGANIZATION_INVITATION_REVOKED,
                    actor=actor,
                    ctx=ctx,
                    target=(AuditTarget.INVITATION, invitation_id),
                    app_key=app.key,
                    org_id=org_id,
                    changes={"reason": "member_removed"},
                )

        await self.audit.record(
            AuditAction.ORGANIZATION_MEMBER_REMOVED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.USER, target_user_id),
            app_key=app.key,
            org_id=org_id,
            changes={"role_keys": role_keys, "count": len(role_keys), "was_owner": was_owner},
        )
        logger.info(
            "org_member_removed org_id=%s app=%s target=%s count=%s",
            org_id,
            app.key,
            target_user_id,
            len(role_keys),
        )
