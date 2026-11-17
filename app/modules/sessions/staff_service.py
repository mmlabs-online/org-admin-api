"""Staff session management (USR-10, spec §5.10): list and revoke a user's account sessions.

Account sessions only: a device session may carry other people's accounts. Lock order: the user
row (`FOR NO KEY UPDATE`), then `account_sessions` rows, as password reset does.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DENIED_ERRORS, NotFoundError, SelfSessionChangeForbiddenError
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.diff import change
from app.modules.audit.service import AuditLogger
from app.modules.rbac.actor import Actor, actor_user_id, require_super
from app.modules.rbac.repository import RbacRepository
from app.modules.sessions.repository import SessionRepository
from app.modules.users.repository import UserRepository

AUDIT_IDS_CAP = 100


@dataclass(frozen=True)
class UserSessionView:
    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime
    device_last_seen_at: datetime
    ip: str | None
    user_agent: str | None


class StaffSessionService:
    def __init__(self, session: AsyncSession, *, idle_days: int, max_days: int) -> None:
        self.users = UserRepository(session)
        self.rbac = RbacRepository(session)
        self.sessions = SessionRepository(session)
        self.audit = AuditLogger(session)
        self.idle = timedelta(days=idle_days)
        self.max_age = timedelta(days=max_days)

    async def list(
        self, user_id: uuid.UUID, *, actor: Actor, limit: int, offset: int
    ) -> tuple[list[UserSessionView], int]:
        # A read: no lock and no audit row (ADR-0009 §8). Never log IP or user agent.
        if await self.users.get(user_id) is None:
            raise NotFoundError("User not found")
        if user_id == actor_user_id(actor):
            raise SelfSessionChangeForbiddenError("Cannot manage your own sessions")
        if await self.rbac.user_has_privileged_assignment(user_id):
            require_super(actor)
        rows, total = await self.sessions.list_active_for_user(
            user_id, idle=self.idle, max_age=self.max_age, limit=limit, offset=offset
        )
        return [
            UserSessionView(
                id=r[0],
                created_at=r[1],
                last_used_at=r[2],
                device_last_seen_at=r[3],
                ip=None if r[4] is None else str(r[4]),
                user_agent=r[5],
            )
            for r in rows
        ], total

    async def _lock_and_guard(
        self, user_id: uuid.UUID, action: AuditAction, *, actor: Actor, ctx: AuditContext
    ) -> uuid.UUID:
        user = await self.users.get_for_update(user_id)
        if user is None:
            raise NotFoundError("User not found")
        uid = user.id  # the denied rollback expires ORM objects
        try:
            if uid == actor_user_id(actor):
                raise SelfSessionChangeForbiddenError("Cannot manage your own sessions")
            if await self.rbac.user_has_privileged_assignment(uid):  # G5
                require_super(actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                action, exc=exc, actor=actor, ctx=ctx, target=(AuditTarget.USER, uid)
            )
            raise
        return uid

    async def _record(
        self,
        action: AuditAction,
        uid: uuid.UUID,
        ids: Sequence[uuid.UUID],
        *,
        actor: Actor,
        ctx: AuditContext,
    ) -> None:
        await self.audit.record(
            action,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.USER, uid),
            changes={
                "account_session_ids": change(None, sorted(str(i) for i in ids)[:AUDIT_IDS_CAP]),
                "account_sessions_revoked": change(None, len(ids)),
            },
        )

    async def revoke_one(
        self, user_id: uuid.UUID, session_id: uuid.UUID, *, actor: Actor, ctx: AuditContext
    ) -> None:
        action = AuditAction.USER_SESSION_REVOKED
        uid = await self._lock_and_guard(user_id, action, actor=actor, ctx=ctx)
        if not await self.sessions.revoke_account_of_user(uid, session_id):
            raise NotFoundError("Session not found")
        await self._record(action, uid, [session_id], actor=actor, ctx=ctx)

    async def revoke_all(self, user_id: uuid.UUID, *, actor: Actor, ctx: AuditContext) -> None:
        action = AuditAction.USER_SESSIONS_REVOKED
        uid = await self._lock_and_guard(user_id, action, actor=actor, ctx=ctx)
        ids = await self.sessions.revoke_all_accounts_of_user_ids(uid)
        if not ids:
            return  # a no-op writes no audit row
        await self._record(action, uid, ids, actor=actor, ctx=ctx)
