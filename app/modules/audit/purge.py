"""Audit retention purge (ADM-004c, ADR-0009 amendment). CLI-only: imported by `app/cli.py` alone.

The only `app/` module that sets the `audit.allow_purge` latch (a test enforces it). The trigger
still refuses rows 90 days old or newer, whatever this module does; `MIN_DAYS` mirrors that floor.
Deletes in batches, one transaction per batch, each with its `audit_log.purged` summary row (R4).
`audit_log.purged` rows themselves are never purged.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, and_, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.actions import AuditAction
from app.modules.audit.context import AuditContext
from app.modules.audit.models import AuditLog
from app.modules.audit.service import AuditLogger
from app.modules.rbac.actor import SYSTEM_CLI

MIN_DAYS = 90  # must equal the floor in migration d7e4b2a91c3f
MAX_DAYS = 3650
BATCH_SIZE = 1000


@dataclass(frozen=True)
class PurgeResult:
    cutoff: datetime
    older_than_days: int
    deleted: int  # or, on a dry run, rows that would be deleted


class AuditPurgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cutoff(self, days: int) -> datetime:
        """`now() - days` on the database clock; refuses anything below the floor."""
        if type(days) is not int or days < MIN_DAYS:
            raise ValueError("retention age is below the minimum")
        value = await self.session.scalar(
            text("SELECT now() - make_interval(days => :n)"), {"n": days}
        )
        return cast(datetime, value)

    async def start_read_only(self) -> None:
        """Must be the first statement of the transaction (dry run)."""
        await self.session.execute(text("SET TRANSACTION READ ONLY"))

    async def count(self, cutoff: datetime) -> int:
        query = select(func.count()).select_from(AuditLog).where(_purgeable(cutoff))
        return int(await self.session.scalar(query) or 0)

    async def open_batch(self) -> None:
        await self.session.execute(text("SET LOCAL lock_timeout = '2s'"))
        await self.session.execute(text("SET LOCAL audit.allow_purge = 'on'"))

    async def delete_batch(self, cutoff: datetime, batch: int) -> int:
        """Delete up to `batch` of the oldest purgeable rows, skipping rows another holds."""
        ids = (
            select(AuditLog.id)
            .where(_purgeable(cutoff))
            .order_by(AuditLog.occurred_at, AuditLog.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        stmt = delete(AuditLog).where(AuditLog.id.in_(ids))
        result = await self.session.execute(stmt.execution_options(synchronize_session=False))
        return int(cast(CursorResult[Any], result).rowcount)

    async def close_batch(self) -> None:
        await self.session.execute(text("SET LOCAL audit.allow_purge = 'off'"))

    async def commit(self) -> None:
        await self.session.commit()


def _purgeable(cutoff: datetime) -> ColumnElement[bool]:
    """Older than the cutoff; the purge trail itself is kept forever."""
    return and_(
        AuditLog.occurred_at < cutoff, AuditLog.action != AuditAction.AUDIT_LOG_PURGED.value
    )


class AuditPurgeService:
    def __init__(self, session: AsyncSession, *, batch_size: int = BATCH_SIZE) -> None:
        self.repo = AuditPurgeRepository(session)
        self.audit = AuditLogger(session)
        self.batch_size = batch_size

    async def run(self, days: int, *, dry_run: bool) -> PurgeResult:
        """One cutoff for the whole run. A dry run only counts, in a READ ONLY transaction that
        the caller's scope rolls back; it never sets the latch."""
        repo = self.repo
        if dry_run:
            await repo.start_read_only()
            cutoff = await repo.cutoff(days)
            return PurgeResult(cutoff, days, await repo.count(cutoff))
        cutoff = await repo.cutoff(days)
        await repo.commit()  # nothing written; ends the snapshot before the first batch
        total = 0
        while True:
            await repo.open_batch()
            deleted = await repo.delete_batch(cutoff, self.batch_size)
            await repo.close_batch()
            if deleted > 0:  # raises before commit on failure: the batch rolls back with it
                await self.audit.record(
                    AuditAction.AUDIT_LOG_PURGED,
                    actor=SYSTEM_CLI,
                    ctx=AuditContext.NONE,
                    changes={
                        "cutoff": cutoff.isoformat(),
                        "older_than_days": days,
                        "deleted": deleted,
                    },
                )
            await repo.commit()
            total += deleted
            if deleted < self.batch_size:
                return PurgeResult(cutoff, days, total)
