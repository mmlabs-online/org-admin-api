"""End-user organizations (USR-06a, D10): create an org as its owner, list the caller's orgs.

Cookie-class callers (R3 class 2). Permission checks use app-scope permissions only (`org_id`
None), never the app+org union: a permission held in some org must not allow creating one.
"""

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.service import AuditLogger
from app.modules.auth.errors import OriginNotAllowedError
from app.modules.organizations.errors import (
    OrgCreateForbiddenError,
    OrgCreateUnavailableError,
    OrgLimitReachedError,
)
from app.modules.organizations.models import Organization, OrganizationStatus
from app.modules.organizations.repository import OrganizationRepository
from app.modules.organizations.schemas import AuthOrgCreate, AuthOrgRead, MyOrgRead
from app.modules.rbac.actor import SYSTEM_ORG_CREATE
from app.modules.rbac.authz import AuthzService, permits
from app.modules.rbac.models import App, AppStatus, JoinPolicy, OrgMode
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import AssignmentService, assignment_changes
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import ResolvedDevice
from app.modules.tokens.errors import EmailUnverifiedError, NotMemberError, OrgNotSupportedError
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)

ORG_CREATE_ACTION = "org:create"  # permission `<app>:org:create`, checked at app scope


def _org_not_supported() -> OrgNotSupportedError:
    return OrgNotSupportedError(
        "This app does not use organizations",
        details=[{"field": "app", "reason": "org_mode_none"}],
    )


class OrgSelfService:
    def __init__(self, session: AsyncSession, *, max_per_user: int) -> None:
        self.repo = OrganizationRepository(session)
        self.rbac = RbacRepository(session)
        self.authz = AuthzService(session)
        self.assignments = AssignmentService(session)
        self.accounts = SessionRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditLogger(session)
        self.max_per_user = max_per_user

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

    async def create(
        self, calling_app: App, device: ResolvedDevice, data: AuthOrgCreate, *, ctx: AuditContext
    ) -> AuthOrgRead:
        # ADR-0003 §10: app lock first, then users.
        app = await self.rbac.lock_app(calling_app.key)
        if app is None or app.status != AppStatus.ACTIVE.value:
            raise OriginNotAllowedError("Origin is not allowed")
        if app.org_mode != OrgMode.REQUIRED.value:
            raise _org_not_supported()
        user = await self._user(device, data.account_id)

        app_permissions = await self.authz.effective_permissions(user.id, app.key)
        if not await self.authz.effective_roles(user.id, app.key):
            join_available = (
                app.join_policy == JoinPolicy.OPEN.value and app.default_role_id is not None
            )
            raise NotMemberError(
                "Not a member of this app", details={"join_available": join_available}
            )
        if not permits(app_permissions, f"{app.key}:{ORG_CREATE_ACTION}"):
            raise OrgCreateForbiddenError("Not allowed to create organizations")

        role = (
            None
            if app.org_owner_role_id is None
            else await self.rbac.get_role(app.id, app.org_owner_role_id)
        )
        if role is None or await self.rbac.role_has_admin_permission(role.id):
            logger.warning("org_owner_role_unusable app_id=%s", app.id)
            raise OrgCreateUnavailableError("Organizations cannot be created right now")
        if await self.repo.count_created_by(user.id) >= self.max_per_user:
            raise OrgLimitReachedError("Organization limit reached")

        if await self.repo.get_by_slug(data.slug):
            raise ConflictError(f"Slug '{data.slug}' is already taken")
        try:
            org = await self.repo.add(
                Organization(
                    slug=data.slug,
                    name=data.name,
                    description=data.description,
                    created_by=user.id,
                )
            )
        except IntegrityError as exc:  # concurrent insert with the same slug
            raise ConflictError(f"Slug '{data.slug}' is already taken") from exc

        ra, _ = await self.assignments._grant_locked(
            app, role, user.id, org.id, None, actor=SYSTEM_ORG_CREATE
        )
        actor = EndUser(user.id)
        await self.audit.record(
            AuditAction.ORGANIZATION_CREATED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ORGANIZATION, org.id),
            app_key=app.key,
            org_id=org.id,
            changes=diff.diff(None, diff.snapshot(org, diff.ORG)),
        )
        await self.audit.record(
            AuditAction.ROLE_ASSIGNMENT_GRANTED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.ROLE_ASSIGNMENT, ra.id),
            app_key=app.key,
            org_id=org.id,
            changes={
                k: v
                for k, v in assignment_changes(ra, role.key, granted=True).items()
                if k in {"role_key", "org_id"}
            },
        )
        logger.info("org_created user_id=%s app=%s org_id=%s", user.id, app.key, org.id)
        return AuthOrgRead(
            id=org.id,
            slug=org.slug,
            name=org.name,
            description=org.description,
            status=OrganizationStatus(org.status),
            created_at=org.created_at,
            updated_at=org.updated_at,
            roles=[role.key],
        )

    async def list_mine(
        self,
        app: App,
        device: ResolvedDevice,
        account_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[MyOrgRead], int]:
        if app.org_mode != OrgMode.REQUIRED.value:
            raise _org_not_supported()
        user = await self._user(device, account_id)
        rows, total = await self.repo.list_for_user_app(user.id, app.id, limit=limit, offset=offset)
        return [
            MyOrgRead(
                id=o.id,
                slug=o.slug,
                name=o.name,
                status=OrganizationStatus(o.status),
                roles=list(roles),
            )
            for o, roles in rows
        ], total
