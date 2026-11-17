"""Staff decisions on join requests (USR-03g). HTTP-free (R1).

Lock order (ADR-0003 §10): app (`FOR NO KEY UPDATE`) -> user (`FOR SHARE`) -> request
(`FOR UPDATE`). The app lock also serialises the cookie-side join, so approve, reject and join
never race for one app.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.diff import change
from app.modules.audit.service import AuditLogger
from app.modules.join_requests.models import AppJoinRequest, JoinRequestStatus
from app.modules.join_requests.repository import JoinRequestRepository
from app.modules.join_requests.schemas import JoinRequestRead
from app.modules.membership.errors import JoinUnavailableError
from app.modules.membership.service import usable_default_role
from app.modules.rbac.actor import Actor, actor_user_id
from app.modules.rbac.authz import AuthzService
from app.modules.rbac.models import ADMIN_APP_KEY, App, AppStatus, JoinPolicy
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import AssignmentService
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)


def _refusal(message: str, reason: str) -> ConflictError:
    return ConflictError(message, details=[{"field": "request_id", "reason": reason}])


def _read(app_key: str, row: AppJoinRequest, user: User) -> JoinRequestRead:
    return JoinRequestRead(
        id=row.id,
        app_key=app_key,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        email_verified=user.email_verified_at is not None,
        user_status=user.status,
        status=row.status,
        created_at=row.created_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
    )


class JoinRequestService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = JoinRequestRepository(session)
        self.rbac = RbacRepository(session)
        self.authz = AuthzService(session)
        self.assignments = AssignmentService(session)
        self.users = UserRepository(session)
        self.audit = AuditLogger(session)

    async def list(
        self, key: str, status: str, *, limit: int, offset: int
    ) -> tuple[list[JoinRequestRead], int]:
        app = None if key == ADMIN_APP_KEY else await self.rbac.get_app_by_key(key)
        if app is None:
            raise NotFoundError("App not found")
        rows, total = await self.repo.page(app.id, status, limit=limit, offset=offset)
        return [_read(app.key, r, u) for r, u in rows], total

    async def _locked_app(self, key: str) -> App:
        app = None if key == ADMIN_APP_KEY else await self.rbac.lock_app(key)
        if app is None:
            raise NotFoundError("App not found")
        return app

    async def _pending(self, app: App, request_id: uuid.UUID, *, lock_user: bool) -> AppJoinRequest:
        """Lock app -> (user) -> request; 404 for an unknown or another app's id."""
        row = await self.repo.get_in_app(app.id, request_id)
        if row is None:
            raise NotFoundError("Join request not found")
        if lock_user:
            await self.users.get_for_share(row.user_id)
        row = await self.repo.get_in_app_for_update(app.id, request_id)
        if row is None:
            raise NotFoundError("Join request not found")
        if row.status != JoinRequestStatus.PENDING.value:
            raise _refusal("Join request was already decided", "not_pending")
        return row

    async def approve(
        self, key: str, request_id: uuid.UUID, *, actor: Actor, ctx: AuditContext
    ) -> JoinRequestRead:
        app = await self._locked_app(key)
        row = await self._pending(app, request_id, lock_user=True)
        if app.status != AppStatus.ACTIVE.value:
            raise _refusal("App is disabled", "app_disabled")
        if app.join_policy != JoinPolicy.APPROVAL.value:
            raise _refusal("App no longer requires approval", "join_policy_changed")
        user = await self.repo.get_user(row.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise _refusal("User is not active", "user_disabled")
        if user.email_verified_at is None:
            raise _refusal("User email is not verified", "email_unverified")
        try:
            role = await usable_default_role(self.rbac, app)
        except JoinUnavailableError as exc:
            raise _refusal("App default role is not usable", "default_role_unusable") from exc

        changes = {"status": change("pending", "approved")}
        if not await self.authz.effective_roles(user.id, app.key):  # already a member: no grant
            await self.assignments._grant_locked(app, role, user.id, None, None, actor=actor)
            changes["role_key"] = change(None, role.key)
        await self.repo.decide(row, JoinRequestStatus.APPROVED, actor_user_id(actor))
        await self.audit.record(
            AuditAction.APP_JOIN_REQUEST_APPROVED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.JOIN_REQUEST, row.id),
            app_key=app.key,
            changes=changes,
        )
        logger.info(
            "join_request_approved request_id=%s app=%s user_id=%s", row.id, app.key, user.id
        )
        return _read(app.key, row, user)

    async def reject(
        self, key: str, request_id: uuid.UUID, *, actor: Actor, ctx: AuditContext
    ) -> JoinRequestRead:
        app = await self._locked_app(key)
        row = await self._pending(app, request_id, lock_user=False)
        user = await self.repo.get_user(row.user_id)
        if user is None:  # unreachable: the FK cascades the request with the user
            raise NotFoundError("Join request not found")
        await self.repo.decide(row, JoinRequestStatus.REJECTED, actor_user_id(actor))
        await self.audit.record(
            AuditAction.APP_JOIN_REQUEST_REJECTED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.JOIN_REQUEST, row.id),
            app_key=app.key,
            changes={"status": change("pending", "rejected")},
        )
        logger.info(
            "join_request_rejected request_id=%s app=%s user_id=%s", row.id, app.key, user.id
        )
        return _read(app.key, row, user)
