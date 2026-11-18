"""Retention cleanup of decided join requests (USR-15), run by `python -m app.cli cleanup`.

Batches, one commit per batch, one `join_request_retention.purged` summary per deleting batch in
the same transaction. Pending rows are never deleted; rejections inside the cooldown are kept.
"""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.actions import AuditAction
from app.modules.audit.context import AuditContext
from app.modules.audit.diff import change
from app.modules.audit.service import AuditLogger
from app.modules.auth.retention import BATCH_SIZE, PurgeCount
from app.modules.join_requests.repository import JoinRequestRetentionRepository
from app.modules.rbac.actor import SYSTEM_CLI


class JoinRequestRetentionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        retention: timedelta,
        cooldown_seconds: int,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.repo = JoinRequestRetentionRepository(session)
        self.audit = AuditLogger(session)
        self.retention = retention
        self.cooldown_seconds = cooldown_seconds
        self.batch_size = batch_size

    async def run(self, *, dry_run: bool) -> list[PurgeCount]:
        label = f"decided, older than {self.retention.days} d"
        if dry_run:
            rows = await self.repo.count(self.retention, self.cooldown_seconds)
            return [PurgeCount("join_requests", rows, label)]
        rows = 0
        while True:
            await self.repo.set_lock_timeout()
            deleted = await self.repo.purge(self.retention, self.cooldown_seconds, self.batch_size)
            if deleted > 0:  # one summary row per deleting batch: a count, never rows
                await self.audit.record(
                    AuditAction.JOIN_REQUEST_RETENTION_PURGED,
                    actor=SYSTEM_CLI,
                    ctx=AuditContext.NONE,
                    changes={"join_requests": change(None, deleted)},
                )
            await self.repo.commit()
            rows += deleted
            if deleted < self.batch_size:
                break
        return [PurgeCount("join_requests", rows, label)]
