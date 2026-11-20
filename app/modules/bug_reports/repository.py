import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bug_reports.models import BugReport, BugReportStatus


@dataclass(frozen=True)
class DailyWindow:
    now: datetime  # database clock
    count: int
    oldest: datetime | None


class BugReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_reporter(self, user_id: uuid.UUID) -> None:
        """Serialize submits of one user until the transaction ends (caps are check-then-insert)."""
        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(literal("bug_report:") + literal(str(user_id)), 0)
                )
            )
        )

    async def daily_window(self, user_id: uuid.UUID) -> DailyWindow:
        """Reports of the user in the last 24 h, on the database clock."""
        now, count, oldest = (
            await self.session.execute(
                select(func.now(), func.count(), func.min(BugReport.created_at)).where(
                    BugReport.user_id == user_id,
                    BugReport.created_at > func.now() - timedelta(hours=24),
                )
            )
        ).one()
        return DailyWindow(now, int(count), oldest)

    async def count_open(self, user_id: uuid.UUID, app_id: uuid.UUID) -> int:
        count = await self.session.scalar(
            select(func.count()).where(
                BugReport.user_id == user_id,
                BugReport.app_id == app_id,
                BugReport.status == BugReportStatus.OPEN.value,
            )
        )
        return int(count or 0)

    async def add(self, report: BugReport) -> BugReport:
        self.session.add(report)
        await self.session.flush()
        await self.session.refresh(report, ["created_at"])
        return report
