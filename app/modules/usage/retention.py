"""Usage event retention purge (ADM-045, OQ69, ADR-0022). CLI-only: imported by `app/cli.py` alone.

Deletes per-use events older than the retention in batches, one transaction per batch, each with
its `usage_events.purged` summary row (R4). `usage_counters` (per-period totals) are never touched.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.actions import AuditAction
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.rbac.actor import SYSTEM_CLI
from app.modules.usage.models import UsageEvent

MIN_DAYS = 90  # ADR-0022: idempotency records are kept at least as long as events
BATCH_SIZE = 1000


@dataclass(frozen=True)
class PurgeResult:
    cutoff: datetime
    retention_days: int
    deleted: int  # or, on a dry run, rows that would be deleted


class UsageEventRetentionRepository:
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
        query = select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at < cutoff)
        return int(await self.session.scalar(query) or 0)

    async def set_lock_timeout(self) -> None:
        await self.session.execute(text("SET LOCAL lock_timeout = '2s'"))

    async def delete_batch(self, cutoff: datetime, batch: int) -> int:
        """Delete up to `batch` of the oldest events, skipping rows another transaction holds."""
        # MATERIALIZED: the ids are selected and locked once; an inlined subquery could be
        # rescanned per outer row and return the next live rows each time (BUG-001).
        batch_ids = (
            select(UsageEvent.id)
            .where(UsageEvent.created_at < cutoff)
            .order_by(UsageEvent.created_at, UsageEvent.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
            .cte("batch")
            .prefix_with("MATERIALIZED")
        )
        stmt = delete(UsageEvent).where(UsageEvent.id.in_(select(batch_ids.c.id)))
        result = await self.session.execute(stmt.execution_options(synchronize_session=False))
        return int(cast(CursorResult[Any], result).rowcount)

    async def commit(self) -> None:
        await self.session.commit()


class UsageEventRetentionService:
    def __init__(self, session: AsyncSession, *, batch_size: int = BATCH_SIZE) -> None:
        self.repo = UsageEventRetentionRepository(session)
        self.audit = AuditLogger(session)
        self.batch_size = batch_size

    async def run(self, days: int, *, apply: bool) -> PurgeResult:
        """One cutoff for the whole run. A dry run only counts, in a READ ONLY transaction that
        the caller's scope rolls back."""
        repo = self.repo
        if not apply:
            await repo.start_read_only()
            cutoff = await repo.cutoff(days)
            return PurgeResult(cutoff, days, await repo.count(cutoff))
        cutoff = await repo.cutoff(days)
        await repo.commit()  # nothing written; ends the snapshot before the first batch
        total = 0
        while True:
            await repo.set_lock_timeout()
            deleted = await repo.delete_batch(cutoff, self.batch_size)
            if deleted > 0:  # raises before commit on failure: the batch rolls back with it
                await self.audit.record(
                    AuditAction.USAGE_EVENTS_PURGED,
                    actor=SYSTEM_CLI,
                    ctx=AuditContext.NONE,
                    changes={
                        "cutoff": cutoff.isoformat(),
                        "retention_days": days,
                        "deleted": deleted,
                    },
                )
            await repo.commit()
            total += deleted
            # A short batch can also mean SKIP LOCKED skipped rows another transaction holds:
            # the run then ends early. Re-running is safe.
            if deleted < self.batch_size:
                return PurgeResult(cutoff, days, total)
