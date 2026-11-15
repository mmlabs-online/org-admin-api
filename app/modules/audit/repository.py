"""Append-only by shape (AC2): `add` and `list` are the only row operations; `rollback`,
`commit` and `begin_nested` touch session state only (ADM-004b failure paths). The DB trigger
backs this up."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

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

    async def list(
        self, filters: AuditLogFilter, *, limit: int, offset: int
    ) -> tuple[list[AuditLog], int]:
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
        if filters.outcome is not None:
            where.append(AuditLog.outcome == filters.outcome)
        if filters.occurred_from is not None:
            where.append(AuditLog.occurred_at >= filters.occurred_from)
        if filters.occurred_to is not None:
            where.append(AuditLog.occurred_at < filters.occurred_to)
        total = await self.session.scalar(select(func.count()).select_from(AuditLog).where(*where))
        result = await self.session.execute(
            select(AuditLog)
            .where(*where)
            .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), total or 0
