import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    DENIED_ERRORS,
    LastSuperAdminError,
    NotFoundError,
    SelfStatusChangeForbiddenError,
    UnprocessableError,
)
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.diff import change
from app.modules.audit.service import AuditLogger
from app.modules.auth.repository import EmailTokenRepository
from app.modules.join_requests.repository import JoinRequestRepository
from app.modules.rbac.actor import Actor, actor_user_id, require_super
from app.modules.rbac.models import ADMIN_APP_KEY, AppStatus
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import SUPER_ADMIN_ROLE
from app.modules.sessions.repository import SessionRepository
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import AccessRow, UserRepository
from app.modules.users.schemas import AccessRole, AppAccess, UserUpdate

logger = logging.getLogger(__name__)

ACCESS_CAP = 20


def _filter_unprocessable(message: str, field: str, reason: str) -> UnprocessableError:
    return UnprocessableError(message, details=[{"field": field, "reason": reason}])


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = UserRepository(session)
        self.audit = AuditLogger(session)

    async def get(self, user_id: uuid.UUID) -> User:
        user = await self.repo.get(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def access_summaries(
        self, user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[list[AppAccess], bool]]:
        """Active access per user: first `ACCESS_CAP` rows grouped by app, plus `truncated`.

        Every requested id gets an entry (`([], False)` when the user has none). Read-only.
        """
        per_user: dict[uuid.UUID, list[AccessRow]] = {uid: [] for uid in user_ids}
        for row in await self.repo.list_access(user_ids, per_user=ACCESS_CAP + 1):
            per_user[row.user_id].append(row)
        result: dict[uuid.UUID, tuple[list[AppAccess], bool]] = {}
        for uid, rows in per_user.items():
            groups: list[AppAccess] = []
            for row in rows[:ACCESS_CAP]:
                if not groups or groups[-1].app != row.app_key:
                    groups.append(
                        AppAccess(app=row.app_key, app_status=AppStatus(row.app_status), roles=[])
                    )
                groups[-1].roles.append(
                    AccessRole(
                        assignment_id=row.assignment_id,
                        role_id=row.role_id,
                        role_key=row.role_key,
                        org_id=row.org_id,
                        expires_at=row.expires_at,
                    )
                )
            result[uid] = (groups, len(rows) > ACCESS_CAP)
        return result

    async def list(
        self,
        *,
        email: str | None,
        status: UserStatus | None,
        app_key: str | None = None,
        role_id: uuid.UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[User], int]:
        """Unknown `app_key` / `role_id` are 422, not an empty page (spec §5.15, OQ76)."""
        app_id: uuid.UUID | None = None
        rbac = RbacRepository(self.repo.session)
        if app_key is not None:
            app = await rbac.get_app_by_key(app_key)
            if app is None:
                raise _filter_unprocessable("Unknown app", "app", "unknown_app")
            app_id = app.id
        if role_id is not None:
            found = await rbac.role_with_app(role_id)
            if found is None:
                raise _filter_unprocessable("Unknown role", "role_id", "unknown_role")
            if app_id is not None and found[0].app_id != app_id:
                raise _filter_unprocessable(
                    "Role does not belong to this app", "role_id", "role_not_in_app"
                )
            app_id = None  # the role already pins the app
        return await self.repo.list(
            email=email,
            status=status,
            app_id=app_id,
            role_id=role_id,
            limit=limit,
            offset=offset,
        )

    async def update(
        self, user_id: uuid.UUID, body: UserUpdate, *, actor: Actor, ctx: AuditContext
    ) -> User:
        """Status first (its own lock order), then the name; one transaction."""
        if body.status is not None:
            user = await self.set_status(user_id, body.status, actor=actor, ctx=ctx)
            if "display_name" not in body.model_fields_set:
                return user
        return await self.set_display_name(user_id, body.display_name, actor=actor, ctx=ctx)

    async def set_display_name(
        self, user_id: uuid.UUID, value: str | None, *, actor: Actor, ctx: AuditContext
    ) -> User:
        """G5: a privileged target needs `admin:*`. The value is never logged or audited (R11)."""
        user = await self.repo.get_for_update(user_id)
        if user is None:
            raise NotFoundError("User not found")
        if user.display_name == value:
            return user  # no write: updated_at stays unchanged
        try:
            if await RbacRepository(self.repo.session).user_has_privileged_assignment(user.id):
                require_super(actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.USER_DISPLAY_NAME_CHANGED,
                exc=exc,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.USER, user_id),
            )
            raise
        await self.repo.set_display_name(user, value)
        await self.audit.record(
            AuditAction.USER_DISPLAY_NAME_CHANGED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.USER, user.id),
            changes={"display_name": {"set": value is not None}},
        )
        logger.info("display_name_changed user_id=%s cleared=%s via=staff", user.id, value is None)
        return user

    async def set_status(
        self, user_id: uuid.UUID, status: UserStatus, *, actor: Actor, ctx: AuditContext
    ) -> User:
        """Lock order `admin` app -> user row (as `bootstrap_super_admin`) -> the user's join
        requests (disable cancels them), so the last-super-admin guard is serialized with revokes
        and other status changes. No per-app lock: user before app would deadlock with approve."""
        rbac = RbacRepository(self.repo.session)
        admin = await rbac.lock_app(ADMIN_APP_KEY)
        user = await self.repo.get_for_update(user_id)
        if user is None:
            raise NotFoundError("User not found")
        if user.status == status.value:
            return user  # no write: updated_at stays unchanged
        action = (
            AuditAction.USER_DISABLED if status == UserStatus.DISABLED else AuditAction.USER_ENABLED
        )
        try:
            if user.id == actor_user_id(actor):
                raise SelfStatusChangeForbiddenError("Cannot change your own status")
            if await rbac.user_has_privileged_assignment(user.id):  # G5: disable and re-enable
                require_super(actor)
            if status == UserStatus.DISABLED and admin is not None:
                role = await rbac.get_role_by_key(admin.id, SUPER_ADMIN_ROLE)
                ra = None if role is None else await rbac.find_assignment(user.id, role.id, None)
                if (
                    role is not None
                    and ra is not None
                    and await rbac.is_counting_super_admin(role.id, ra.id)
                    and await rbac.count_other_active_super_admins(role.id, ra.id) == 0
                ):
                    raise LastSuperAdminError("Cannot disable the last active super-admin")
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                action, exc=exc, actor=actor, ctx=ctx, target=(AuditTarget.USER, user_id)
            )
            raise
        old_status = user.status
        user.status = status.value
        await self.repo.session.flush()
        if status == UserStatus.DISABLED:
            # Admin tokens stop on the next request (per-request reload); app tokens expire.
            await SessionRepository(self.repo.session).revoke_all_accounts_of_user(user.id)
            # A reset/verify link must not come back to life on re-enable (ADM-002d).
            await EmailTokenRepository(self.repo.session).invalidate_all(user.id)
            cancelled = await JoinRequestRepository(self.repo.session).cancel_pending_for_user(
                user.id, actor_user_id(actor)
            )
            for request_id, app_key in cancelled:
                await self.audit.record(
                    AuditAction.APP_JOIN_REQUEST_CANCELLED,
                    actor=actor,
                    ctx=ctx,
                    target=(AuditTarget.JOIN_REQUEST, request_id),
                    app_key=app_key,
                    changes={"status": change("pending", "cancelled")},
                )
        await self.audit.record(
            action,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.USER, user.id),
            changes={"status": change(old_status, status.value)},
        )
        await self.repo.session.refresh(user)
        return user
