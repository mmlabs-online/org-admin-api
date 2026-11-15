"""`AuditLogger`: the write paths (R4, ADR-0009 amendment); `AuditLogService`: reads."""

import contextlib
import ipaddress
import logging
import re
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, UnprocessableError
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.audit.actions import ActorKind, AuditAction, AuditOutcome, AuditTarget
from app.modules.audit.context import AnonymousActor, AuditContext, EndUser
from app.modules.audit.diff import to_json
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditLogFilter, AuditRepository
from app.modules.rbac.actor import Actor, SystemActor

logger = logging.getLogger(__name__)

USER_AGENT_MAX = 512
_FORBIDDEN_KEY = re.compile(r"hash|token|password|cookie|email|secret", re.IGNORECASE)
# Table names of the retention summary: row counts, never values (`email_retention.purged` only).
_ALLOWED_KEYS = frozenset({"email_send_log", "email_tokens"})
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

AuditActor = Actor | EndUser | AnonymousActor


def _valid_ip(value: str | None) -> str | None:
    """None for an invalid or scoped (`%zone`) address; IPv4-mapped IPv6 as plain IPv4."""
    if value is None:
        return None
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.scope_id is not None:
            return None
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
    return str(addr)


def _clean_user_agent(value: str | None) -> str | None:
    if value is None:
        return None
    return _CONTROL_CHARS.sub("", value)[:USER_AGENT_MAX] or None


def _is_count(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"from", "to"}:
        return False
    to = value["to"]
    return value["from"] is None and isinstance(to, int) and not isinstance(to, bool)


def _check_keys(action: AuditAction, changes: Mapping[str, Any]) -> None:
    """Rejects secret-looking keys before any SQL (R11)."""
    for key, value in changes.items():
        if _FORBIDDEN_KEY.search(key) is None:
            continue
        if (
            action is AuditAction.EMAIL_RETENTION_PURGED
            and key in _ALLOWED_KEYS
            and _is_count(value)
        ):
            continue
        raise ValueError(f"forbidden audit change key: {key}")


def _build(
    action: AuditAction,
    *,
    outcome: AuditOutcome,
    actor: AuditActor,
    ctx: AuditContext,
    target: tuple[AuditTarget, uuid.UUID] | None,
    app_key: str | None,
    org_id: uuid.UUID | None,
    changes: Mapping[str, Any] | None,
) -> AuditLog:
    _check_keys(action, changes or {})
    kind: ActorKind
    user_id: uuid.UUID | None
    label: str | None
    if isinstance(actor, SystemActor):
        kind, user_id, label = ActorKind.SYSTEM, None, actor.name
    elif isinstance(actor, AnonymousActor):
        kind, user_id, label = ActorKind.ANONYMOUS, None, None
    else:
        kind, user_id, label = ActorKind.USER, actor.user_id, None
    return AuditLog(
        actor_kind=kind.value,
        actor_user_id=user_id,
        actor_label=label,
        action=action.value,
        outcome=outcome.value,
        target_type=None if target is None else target[0].value,
        target_id=None if target is None else target[1],
        app_key=app_key,
        org_id=org_id,
        changes=to_json(dict(changes)) if changes else None,
        ip=_valid_ip(ctx.ip),
        user_agent=_clean_user_agent(ctx.user_agent),
        request_id=ctx.request_id,
    )


class AuditLogger:
    """Admin mutations store `{field: {from, to}}` in `changes`; identity and failure rows a
    flat object of allowlisted scalars (ADR-0009 amendment)."""

    def __init__(self, session: AsyncSession) -> None:
        self.repo = AuditRepository(session)

    async def record(
        self,
        action: AuditAction,
        *,
        actor: AuditActor,
        ctx: AuditContext,
        target: tuple[AuditTarget, uuid.UUID] | None = None,
        app_key: str | None = None,
        org_id: uuid.UUID | None = None,
        changes: Mapping[str, Any] | None = None,
    ) -> None:
        """Success path: adds and flushes one row; never commits (the request or CLI scope owns
        the transaction) and raises on error, so the change and its row are atomic (R4)."""
        await self.repo.add(
            _build(
                action,
                outcome=AuditOutcome.SUCCESS,
                actor=actor,
                ctx=ctx,
                target=target,
                app_key=app_key,
                org_id=org_id,
                changes=changes,
            )
        )

    async def record_failure(
        self,
        action: AuditAction,
        *,
        outcome: AuditOutcome,
        actor: AuditActor,
        ctx: AuditContext,
        target: tuple[AuditTarget, uuid.UUID] | None = None,
        app_key: str | None = None,
        org_id: uuid.UUID | None = None,
        changes: Mapping[str, Any] | None = None,
    ) -> None:
        """Failure path, right before the caller raises its intended error: rolls back the
        pending work, then adds and commits the row alone. Takes primitives only (pending ORM
        objects are expired by the rollback). An audit error is logged, never raised, so the
        intended error is always returned; `CancelledError` still propagates."""
        if outcome is AuditOutcome.SUCCESS:
            raise ValueError("record_failure needs a failure or denied outcome")
        try:
            row = _build(
                action,
                outcome=outcome,
                actor=actor,
                ctx=ctx,
                target=target,
                app_key=app_key,
                org_id=org_id,
                changes=changes,
            )
            await self.repo.rollback()
            await self.repo.add(row)
            await self.repo.commit()
        except Exception:
            logger.warning("audit_write_failed action=%s outcome=%s", action.value, outcome.value)
            with contextlib.suppress(Exception):
                await self.repo.rollback()

    async def record_denied(
        self,
        action: AuditAction,
        *,
        exc: AppError,
        actor: Actor,
        ctx: AuditContext,
        target: tuple[AuditTarget, uuid.UUID] | None = None,
        app_key: str | None = None,
        org_id: uuid.UUID | None = None,
        facts: Mapping[str, Any] | None = None,
    ) -> None:
        """A guard refused a staff principal (`outcome=denied`); system actors write nothing."""
        if not isinstance(actor, AdminPrincipal):
            return
        await self.record_failure(
            action,
            outcome=AuditOutcome.DENIED,
            actor=actor,
            ctx=ctx,
            target=target,
            app_key=app_key,
            org_id=org_id,
            changes={"reason": exc.code, **(facts or {})},
        )

    async def record_in_savepoint(
        self,
        action: AuditAction,
        *,
        actor: AuditActor,
        ctx: AuditContext,
        target: tuple[AuditTarget, uuid.UUID] | None = None,
        app_key: str | None = None,
        changes: Mapping[str, Any] | None = None,
    ) -> None:
        """A success row inside a savepoint, for work that the caller commits before raising
        (device-session reuse): an audit error is logged and never undoes the outer work."""
        try:
            async with self.repo.begin_nested():
                await self.repo.add(
                    _build(
                        action,
                        outcome=AuditOutcome.SUCCESS,
                        actor=actor,
                        ctx=ctx,
                        target=target,
                        app_key=app_key,
                        org_id=None,
                        changes=changes,
                    )
                )
        except Exception:
            logger.warning(
                "audit_write_failed action=%s outcome=%s", action.value, AuditOutcome.SUCCESS.value
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
