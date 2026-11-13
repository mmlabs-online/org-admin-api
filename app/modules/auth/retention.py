"""Retention cleanup of the email send log and used/expired email tokens (USR-04d, ADR-0007).

Run by the operator CLI (`python -m app.cli cleanup`); deletes in batches, one commit per batch,
so a failure leaves earlier batches deleted and a re-run is safe. Takes no lock on `users`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.repository import EmailRetentionRepository

SEND_LOG_RETENTION = timedelta(hours=48)
TOKEN_RETENTION = timedelta(days=7)
BATCH_SIZE = 1000


@dataclass(frozen=True)
class PurgeCount:
    table: str
    rows: int
    retention: str  # human-readable rule, e.g. "older than 48 h"


class EmailRetentionService:
    def __init__(self, session: AsyncSession, *, batch_size: int = BATCH_SIZE) -> None:
        self.repo = EmailRetentionRepository(session)
        self.batch_size = batch_size

    async def run(self, *, dry_run: bool) -> list[PurgeCount]:
        """Rows deleted per table (or that would be, on a dry run: count only, no writes)."""
        repo = self.repo
        targets: tuple[
            tuple[
                str,
                timedelta,
                str,
                Callable[[timedelta], Awaitable[int]],
                Callable[[timedelta, int], Awaitable[int]],
            ],
            ...,
        ] = (
            (
                "email_send_log",
                SEND_LOG_RETENTION,
                "older than 48 h",
                repo.count_send_log,
                repo.purge_send_log,
            ),
            (
                "email_tokens",
                TOKEN_RETENTION,
                "used or expired, older than 7 d",
                repo.count_tokens,
                repo.purge_tokens,
            ),
        )
        counts: list[PurgeCount] = []
        for table, retention, label, count, purge in targets:
            if dry_run:
                rows = await count(retention)
            else:
                rows = 0
                while True:
                    await repo.set_lock_timeout()
                    deleted = await purge(retention, self.batch_size)
                    await repo.commit()
                    rows += deleted
                    if deleted < self.batch_size:
                        break
            counts.append(PurgeCount(table, rows, label))
        return counts
