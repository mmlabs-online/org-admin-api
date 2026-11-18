"""Append-only by shape (AC2): `add` is the only write; `list`, `export`, `get` and
`count_actions_since` are reads; `rollback`,
`commit` and `begin_nested` touch session state only (ADM-004b failure paths). The DB trigger
backs this up."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.modules.audit.actions import AuditTarget
from app.modules.audit.models import AuditLog


@dataclass(frozen=True)
class AuditLogFilter:
    """Combined with AND; `occurred_from` inclusive, `occurred_to` exclusive."""

    actor_user_id: uuid.UUID | None = None
    actor_kind: str | None = None
    action: str | None = None
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    app_key: str | None = None
    org_id: uuid.UUID | None = None
    outcome: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, row: AuditLog) -> None:
        self.session.add(row)
        await self.session.flush()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def commit(self) -> None:
        await self.session.commit()

    def begin_nested(self) -> AsyncSessionTransaction:
        return self.session.begin_nested()

    async def get(self, audit_log_id: uuid.UUID) -> AuditLog | None:
        return await self.session.get(AuditLog, audit_log_id)

    @staticmethod
    def _where(filters: AuditLogFilter) -> list[ColumnElement[bool]]:
        where: list[ColumnElement[bool]] = []
        if filters.actor_user_id is not None:
            where.append(AuditLog.actor_user_id == filters.actor_user_id)
        if filters.actor_kind is not None:
            where.append(AuditLog.actor_kind == filters.actor_kind)
        if filters.action is not None:
            where.append(AuditLog.action == filters.action)
        if filters.target_type is not None:
            where.append(AuditLog.target_type == filters.target_type)
        if filters.target_id is not None:
            where.append(AuditLog.target_id == filters.target_id)
        if filters.app_key is not None:
            where.append(AuditLog.app_key == filters.app_key)
        if filters.org_id is not None:
            where.append(
                or_(
                    and_(
                        AuditLog.target_type == AuditTarget.ORGANIZATION.value,
                        AuditLog.target_id == filters.org_id,
                    ),
                    AuditLog.org_id == filters.org_id,
                )
            )
        if filters.outcome is not None:
            where.append(AuditLog.outcome == filters.outcome)
        if filters.occurred_from is not None:
            where.append(AuditLog.occurred_at >= filters.occurred_from)
        if filters.occurred_to is not None:
            where.append(AuditLog.occurred_at < filters.occurred_to)
        return where

    async def list(
        self, filters: AuditLogFilter, *, limit: int, offset: int
    ) -> tuple[list[AuditLog], int]:
        where = self._where(filters)
        total = await self.session.scalar(select(func.count()).select_from(AuditLog).where(*where))
        result = await self.session.execute(
            select(AuditLog)
            .where(*where)
            .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), total or 0

    async def export(self, filters: AuditLogFilter, *, limit: int) -> Sequence[AuditLog]:
        result = await self.session.execute(
            select(AuditLog)
            .where(*self._where(filters))
            .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def count_actions_since(self, actions: Sequence[str], since: datetime) -> dict[str, int]:
        """Rows per action with `occurred_at >= since` (index range scan on `ix_audit_log_action`).
        No upper bound: rows committed after the transaction started may be included."""
        result = await self.session.execute(
            select(AuditLog.action, func.count())
            .where(AuditLog.action.in_(actions), AuditLog.occurred_at >= since)
            .group_by(AuditLog.action)
        )
        return {action: total for action, total in result}
