"""Org invitations (USR-06b, ADR-0014): create (by a member holding `<app>:org:invite` in that
org) and accept (by the invited verified email).

Cookie-class callers (R3 class 2). Every permission decision reads the caller's permissions in
exactly the one org (`org_permissions`), never the app+org union. The create path never reads
`users` by email and answers the same for every target (no account/member oracle).
"""

import hmac
import logging
import secrets
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.diff import change
from app.modules.audit.service import AuditLogger
from app.modules.auth.email_throttle import (
    DAILY_CAP_RETRY_SECONDS,
    INVITE_DAILY_LIMIT,
    resend_retry_after,
)
from app.modules.auth.email_verification import (
    DailyCapReachedError,
    daily_cap_refusal,
    email_token_hash,
)
from app.modules.auth.errors import OriginNotAllowedError, TokenInvalidError
from app.modules.auth.repository import EmailSendLogRepository, EmailTokenRepository
from app.modules.auth.service import normalize_email
from app.modules.auth.throttle import EmailHasher
from app.modules.email.messages import invitation_message
from app.modules.email.sender import OutgoingEmail
from app.modules.invitations.errors import (
    InvitationEmailMismatchError,
    InvitationLimitReachedError,
    InviteRoleForbiddenError,
    OrgInviteForbiddenError,
    OrgInviteUnavailableError,
    UnknownRoleError,
)
from app.modules.invitations.models import OrgInvitation
from app.modules.invitations.repository import InvitationRepository
from app.modules.invitations.rules import is_subset, roles_overlap, safe_org_name
from app.modules.invitations.schemas import InvitationAccepted, InvitationCreate, InvitationCreated
from app.modules.membership.service import join_default_role
from app.modules.organizations.auth_service import _org_not_supported
from app.modules.organizations.repository import OrganizationRepository
from app.modules.rbac.actor import SYSTEM_JOIN, SYSTEM_ORG_INVITE
from app.modules.rbac.authz import AuthzService, permits
from app.modules.rbac.models import App, AppStatus, JoinPolicy, OrgMode, Role
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import AssignmentService
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import ResolvedDevice
from app.modules.tokens.errors import EmailUnverifiedError, OrgAccessDeniedError
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)

INVITE = "invite"
INVITE_PATH = "/accept-invitation"  # fixed page path on every app (docs/consumers.md)
INVITE_ACTION = "org:invite"  # permission `<app>:org:invite`, checked org-exact


def _token_invalid() -> TokenInvalidError:
    """One answer, with the same empty `details`, for every unusable token."""
    return TokenInvalidError("Token is invalid or expired", details={})


class InvitationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        email_hasher: EmailHasher,
        daily_cap: int,
        ttl_hours: int,
        max_per_inviter_per_day: int,
        max_pending_per_org: int,
    ) -> None:
        self.repo = InvitationRepository(session)
        self.orgs = OrganizationRepository(session)
        self.rbac = RbacRepository(session)
        self.authz = AuthzService(session)
        self.assignments = AssignmentService(session)
        self.accounts = SessionRepository(session)
        self.users = UserRepository(session)
        self.tokens = EmailTokenRepository(session)
        self.sends = EmailSendLogRepository(session)
        self.audit = AuditLogger(session)
        self.email_hasher = email_hasher
        self.daily_cap = daily_cap
        self.ttl = timedelta(hours=ttl_hours)
        self.max_per_inviter_per_day = max_per_inviter_per_day
        self.max_pending_per_org = max_pending_per_org

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

    async def _lock_active_app(self, calling_app: App) -> App:
        # ADR-0003 §10: app lock first, then users.
        app = await self.rbac.lock_app(calling_app.key)
        if app is None or app.status != AppStatus.ACTIVE.value:
            raise OriginNotAllowedError("Origin is not allowed")
        return app

    async def _guard(self, app: App) -> None:
        """S1 guard (ADR-0014): invitations work only while the default role and the org owner
        role share no permission except `<app>:org:create`, so the invitee's app-scope default
        role cannot widen a narrower invited role in the org token. Fails closed."""
        reason = await self._unavailable_reason(app)
        if reason is not None:
            logger.warning("org_invite_unavailable app_id=%s reason=%s", app.id, reason)
            raise OrgInviteUnavailableError("Invitations are not available right now")

    async def _unavailable_reason(self, app: App) -> str | None:
        if app.org_mode != OrgMode.REQUIRED.value:
            return "org_mode"
        if app.join_policy != JoinPolicy.OPEN.value:
            return "join_policy"
        if app.default_role_id is None or app.org_owner_role_id is None:
            return "roles_unset"
        default = await self.rbac.get_role(app.id, app.default_role_id)
        owner = await self.rbac.get_role(app.id, app.org_owner_role_id)
        if default is None or owner is None:
            return "roles_missing"
        if await self.rbac.role_has_admin_permission(default.id):
            return "default_role_admin"
        default_keys = (await self.rbac.role_permission_keys(default.id)).values()
        owner_keys = (await self.rbac.role_permission_keys(owner.id)).values()
        if roles_overlap(app.key, default_keys, owner_keys):
            return "roles_overlap"
        return None

    async def create(
        self,
        calling_app: App,
        device: ResolvedDevice,
        org_id: uuid.UUID,
        data: InvitationCreate,
        link_origin: str,
        *,
        ctx: AuditContext,
    ) -> tuple[InvitationCreated, OutgoingEmail | None]:
        app = await self._lock_active_app(calling_app)
        if app.org_mode != OrgMode.REQUIRED.value:
            raise _org_not_supported()
        user = await self._user(device, data.account_id)

        # Org-exact: one answer for an unknown, suspended or not-a-member org (no oracle).
        if not await self.authz.org_role_keys(user.id, app.key, org_id):
            raise OrgAccessDeniedError("No access to this organization", details={})
        inviter_permissions = await self.authz.org_permissions(user.id, app.key, org_id)
        if not permits(inviter_permissions, f"{app.key}:{INVITE_ACTION}"):
            raise OrgInviteForbiddenError("Not allowed to invite into this organization")
        await self._guard(app)

        role = await self.rbac.get_role_by_key(app.id, data.role)
        if role is None:
            raise UnknownRoleError(
                "Unknown role", details=[{"field": "role", "reason": "unknown_role"}]
            )
        role_keys = (await self.rbac.role_permission_keys(role.id)).values()
        if await self.rbac.role_has_admin_permission(role.id) or not is_subset(
            role_keys, inviter_permissions
        ):
            raise InviteRoleForbiddenError("This role cannot be granted by invitation")

        if (
            await self.repo.count_created_by_24h(user.id) >= self.max_per_inviter_per_day
            or await self.repo.count_pending_for_org(org_id) >= self.max_pending_per_org
        ):
            raise InvitationLimitReachedError("Invitation limit reached")

        email = str(data.email).strip()
        email_hmac = self.email_hasher.hmac(normalize_email(email))
        # Serializes every invite to this address across orgs (also the partial-unique race).
        await self.sends.lock(email_hmac, INVITE)
        # The global cap first: with the cap exhausted every target answers 429, so 201 vs 429
        # does not show that this address was invited recently (R-1). Lock order: email, global.
        reason = await daily_cap_refusal(self.tokens, self.sends, INVITE, self.daily_cap)
        if reason is not None:  # says nothing about the target: not silent (S-2)
            logger.warning("invitation_not_sent reason=%s org_id=%s", reason, org_id)
            raise DailyCapReachedError(DAILY_CAP_RETRY_SECONDS, reason)
        window = await self.sends.window(email_hmac, INVITE)
        actor = EndUser(user.id)
        if (
            resend_retry_after(
                window.now, window.last, window.count, window.oldest, daily_limit=INVITE_DAILY_LIMIT
            )
            is not None
        ):
            # Silent, but the inviter's earlier invitations to this address must not stay live
            # with a role the 201 no longer describes (S-1): supersede this org's own.
            await self._supersede(org_id, app, email_hmac, actor, ctx)
            return await self._silent(org_id, role, "target_limit"), None
        await self._supersede(org_id, app, email_hmac, actor, ctx)
        token = secrets.token_urlsafe(32)
        invitation = await self.repo.add(
            org_id=org_id,
            app_id=app.id,
            role_id=role.id,
            email_hmac=email_hmac,
            token_hash=email_token_hash(token),
            invited_by=user.id,
            ttl=self.ttl,
        )
        await self.sends.insert(email_hmac, INVITE)
        await self.audit.record(
            AuditAction.ORGANIZATION_INVITATION_CREATED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.INVITATION, invitation.id),
            app_key=app.key,
            org_id=org_id,
            changes={"role_key": role.key, "expires_at": invitation.expires_at},
        )

        org = await self.orgs.get(org_id)
        subject, body = invitation_message(
            f"{link_origin}{INVITE_PATH}#token={token}",
            app.name,
            None if org is None else safe_org_name(org.name),
        )
        outgoing = OutgoingEmail(
            to=email, subject=subject, body=body, purpose=INVITE, user_id=user.id
        )
        logger.info(
            "invitation_created invitation_id=%s org_id=%s app=%s", invitation.id, org_id, app.key
        )
        return (
            InvitationCreated(
                id=invitation.id,
                org_id=org_id,
                role=role.key,
                expires_at=invitation.expires_at,
            ),
            outgoing,
        )

    async def _supersede(
        self,
        org_id: uuid.UUID,
        app: App,
        email_hmac: bytes,
        actor: EndUser,
        ctx: AuditContext,
    ) -> None:
        for old_id in await self.repo.supersede_pending(org_id, app.id, email_hmac):
            await self.audit.record(
                AuditAction.ORGANIZATION_INVITATION_REVOKED,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.INVITATION, old_id),
                app_key=app.key,
                org_id=org_id,
                changes={"reason": "superseded"},
            )

    async def _silent(self, org_id: uuid.UUID, role: Role, reason: str) -> InvitationCreated:
        """The same 201 as a real invitation; nothing is queued or sent, so the
        response does not reveal that the target is capped (no email in the log line)."""
        logger.warning("invitation_not_sent reason=%s org_id=%s", reason, org_id)
        return InvitationCreated(
            id=uuid.uuid4(),
            org_id=org_id,
            role=role.key,
            expires_at=await self.repo.db_now() + self.ttl,
        )

    async def accept(
        self,
        calling_app: App,
        device: ResolvedDevice,
        account_id: uuid.UUID,
        token: str,
        *,
        ctx: AuditContext,
    ) -> InvitationAccepted:
        # Lock order: app, invitation row, org (KEY SHARE in `_grant_locked`): as join/org create.
        app = await self._lock_active_app(calling_app)
        user = await self._user(device, account_id)  # unverified: before any token lookup

        row = await self.repo.get_pending_for_update(email_token_hash(token), app.id)
        if row is None:
            raise _token_invalid()
        if not hmac.compare_digest(
            row.email_hmac, self.email_hasher.hmac(normalize_email(user.email))
        ):
            raise InvitationEmailMismatchError("This invitation is for another email address")
        await self._guard(app)
        role = await self._recheck(app, row)

        join_role = None
        if not await self.authz.effective_roles(user.id, app.key):
            join_role = await join_default_role(self.rbac, app)
        if not await self.repo.consume(row.id, user.id):
            raise _token_invalid()

        actor = EndUser(user.id)
        if join_role is not None:
            join_ra, _ = await self.assignments._grant_locked(
                app, join_role, user.id, None, None, actor=SYSTEM_JOIN
            )
            await self.audit.record(
                AuditAction.APP_JOINED,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.ROLE_ASSIGNMENT, join_ra.id),
                app_key=app.key,
                changes={"role_key": change(None, join_role.key)},
            )

        existing = await self.rbac.find_assignment(user.id, role.id, row.org_id)
        granted = existing is None or (
            existing.expires_at is not None and existing.expires_at <= await self.repo.db_now()
        )
        if granted:
            ra, _ = await self.assignments._grant_locked(
                app, role, user.id, row.org_id, None, actor=SYSTEM_ORG_INVITE
            )
            await self.audit.record(
                AuditAction.ROLE_ASSIGNMENT_GRANTED,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.ROLE_ASSIGNMENT, ra.id),
                app_key=app.key,
                org_id=row.org_id,
                changes={"role_key": role.key, "org_id": row.org_id},
            )
        await self.audit.record(
            AuditAction.ORGANIZATION_INVITATION_ACCEPTED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.INVITATION, row.id),
            app_key=app.key,
            org_id=row.org_id,
            changes={"role_key": role.key, "invited_by": row.invited_by, "granted": granted},
        )
        logger.info(
            "invitation_accepted invitation_id=%s user_id=%s org_id=%s granted=%s",
            row.id,
            user.id,
            row.org_id,
            granted,
        )
        return InvitationAccepted(
            org_id=row.org_id,
            roles=await self.authz.org_role_keys(user.id, app.key, row.org_id),
        )

    async def _recheck(self, app: App, row: OrgInvitation) -> Role:
        """The state may have changed since the invitation was created: the role must still be
        admin-free and the inviter active, still holding `org:invite` and every permission of
        the role in that org (this also fails for a suspended org). Else the token is invalid
        and stays pending."""
        role = await self.rbac.get_role(app.id, row.role_id)
        if role is None or await self.rbac.role_has_admin_permission(role.id):
            raise _token_invalid()
        inviter = None if row.invited_by is None else await self.users.get(row.invited_by)
        if inviter is None or inviter.status != UserStatus.ACTIVE.value:
            raise _token_invalid()
        granted = await self.authz.org_permissions(inviter.id, app.key, row.org_id)
        role_keys = (await self.rbac.role_permission_keys(role.id)).values()
        if not permits(granted, f"{app.key}:{INVITE_ACTION}") or not is_subset(role_keys, granted):
            raise _token_invalid()
        return role
