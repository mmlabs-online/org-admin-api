"""`AuditLogger`: the one write path (R4), in the caller's transaction; `AuditLogService`: reads."""

import ipaddress
import re
import uuid
from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableError
from app.modules.audit.actions import ActorKind, AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.diff import Change, to_json
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditLogFilter, AuditRepository
from app.modules.rbac.actor import Actor, SystemActor

USER_AGENT_MAX = 512
_FORBIDDEN_KEY = re.compile(r"hash|token|password|cookie|email|secret", re.IGNORECASE)
# Table names of the retention summary: row counts, never values.
_ALLOWED_KEYS = frozenset({"email_send_log", "email_tokens"})


def _valid_ip(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


class AuditLogger:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = AuditRepository(session)

    async def record(
        self,
        action: AuditAction,
        *,
        actor: Actor | EndUser,
        ctx: AuditContext,
        target: tuple[AuditTarget, uuid.UUID] | None = None,
        app_key: str | None = None,
        org_id: uuid.UUID | None = None,
        changes: Mapping[str, Change] | None = None,
    ) -> None:
        """Adds and flushes one row; never commits (the request or CLI scope owns the
        transaction). Rejects secret-looking keys before any SQL (R11)."""
        for key in changes or {}:
            if key not in _ALLOWED_KEYS and _FORBIDDEN_KEY.search(key):
                raise ValueError(f"forbidden audit change key: {key}")
        if isinstance(actor, SystemActor):
            kind, user_id, label = ActorKind.SYSTEM, None, actor.name
        else:
            kind, user_id, label = ActorKind.USER, actor.user_id, None
        ua = ctx.user_agent[:USER_AGENT_MAX] if ctx.user_agent else None
        await self.repo.add(
            AuditLog(
                actor_kind=kind.value,
                actor_user_id=user_id,
                actor_label=label,
                action=action.value,
                target_type=None if target is None else target[0].value,
                target_id=None if target is None else target[1],
                app_key=app_key,
                org_id=org_id,
                changes=to_json(dict(changes)) if changes else None,
                ip=_valid_ip(ctx.ip),
                user_agent=ua,
                request_id=ctx.request_id,
            )
        )


class AuditLogService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = AuditRepository(session)

    async def list(
        self, filters: AuditLogFilter, *, limit: int, offset: int
    ) -> tuple[list[AuditLog], int]:
        if (
            filters.occurred_from is not None
            and filters.occurred_to is not None
            and filters.occurred_from > filters.occurred_to
        ):
            raise UnprocessableError(
                "from must not be after to", details=[{"field": "from", "reason": "after_to"}]
            )
        return await self.repo.list(filters, limit=limit, offset=offset)
