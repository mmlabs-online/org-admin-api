import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.base import bumped_updated_at
from app.modules.bug_reports.models import BugReport, BugReportAttachment, BugReportStatus
from app.modules.rbac.models import App
from app.modules.users.models import User


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


@dataclass(frozen=True)
class ReportBrief:
    """What the upload checks need, in one query. `now` is the database clock."""

    user_id: uuid.UUID
    app_id: uuid.UUID
    status: str
    created_at: datetime
    now: datetime
    attachment_count: int


class BugReportAttachmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def report_brief(self, report_id: uuid.UUID) -> ReportBrief | None:
        count = (
            select(func.count())
            .where(BugReportAttachment.report_id == BugReport.id)
            .scalar_subquery()
        )
        row = (
            await self.session.execute(
                select(
                    BugReport.user_id,
                    BugReport.app_id,
                    BugReport.status,
                    BugReport.created_at,
                    func.now(),
                    count,
                ).where(BugReport.id == report_id)
            )
        ).first()
        if row is None:
            return None
        user_id, app_id, status, created_at, now, attachments = row
        return ReportBrief(user_id, app_id, status, created_at, now, int(attachments))

    async def lock_report(self, report_id: uuid.UUID) -> tuple[str, int] | None:
        """FOR NO KEY UPDATE (serializes uploads of one report, does not block FK KEY SHARE
        locks); returns the current status and attachment count, or None if the report is gone."""
        status = await self.session.scalar(
            select(BugReport.status)
            .where(BugReport.id == report_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if status is None:
            return None
        return status, await self.count(report_id)

    async def count(self, report_id: uuid.UUID) -> int:
        count = await self.session.scalar(
            select(func.count()).where(BugReportAttachment.report_id == report_id)
        )
        return int(count or 0)

    async def add(self, row: BugReportAttachment) -> BugReportAttachment:
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row, ["created_at"])
        return row

    async def get(
        self, report_id: uuid.UUID, attachment_id: uuid.UUID
    ) -> BugReportAttachment | None:
        """Both ids in the WHERE: an attachment of another report is simply not found."""
        return await self.session.scalar(
            select(BugReportAttachment).where(
                BugReportAttachment.id == attachment_id,
                BugReportAttachment.report_id == report_id,
            )
        )

    async def list_for_report(self, report_id: uuid.UUID) -> list[BugReportAttachment]:
        rows = await self.session.scalars(
            select(BugReportAttachment)
            .where(BugReportAttachment.report_id == report_id)
            .order_by(BugReportAttachment.created_at, BugReportAttachment.id)
        )
        return list(rows.all())

    async def commit(self) -> None:
        """End the transaction (releases connection and row locks before the slow Drive call)."""
        await self.session.commit()


@dataclass(frozen=True)
class ListFilter:
    app_key: str | None = None
    status: str | None = None
    reporter_id: uuid.UUID | None = None
    created_from: datetime | None = None  # inclusive
    created_to: datetime | None = None  # exclusive


@dataclass(frozen=True)
class BugReportView:
    """Plain values for the router. `description` is the 200-character preview in lists."""

    id: uuid.UUID
    app_key: str
    org_id: uuid.UUID | None
    status: str
    reporter_id: uuid.UUID
    reporter_email: str
    reporter_display_name: str | None
    description: str
    page_url: str | None
    app_version: str | None
    user_agent: str | None
    resolution_note: str | None
    handler_id: uuid.UUID | None
    handler_display_name: str | None
    handled_at: datetime | None
    created_at: datetime
    updated_at: datetime


PREVIEW_LENGTH = 200
_REPORTER = aliased(User)
_HANDLER = aliased(User)


def _select(description: Any) -> Any:
    return (
        select(
            BugReport.id,
            App.key,
            BugReport.org_id,
            BugReport.status,
            _REPORTER.id,
            _REPORTER.email,
            _REPORTER.display_name,
            description,
            BugReport.page_url,
            BugReport.app_version,
            BugReport.user_agent,
            BugReport.resolution_note,
            _HANDLER.id,
            _HANDLER.display_name,
            BugReport.handled_at,
            BugReport.created_at,
            BugReport.updated_at,
        )
        .join(App, App.id == BugReport.app_id)
        .join(_REPORTER, _REPORTER.id == BugReport.user_id)
        .outerjoin(_HANDLER, _HANDLER.id == BugReport.handled_by)
    )


def _where(f: ListFilter) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if f.app_key is not None:
        conds.append(App.key == f.app_key)
    if f.status is not None:
        conds.append(BugReport.status == f.status)
    if f.reporter_id is not None:
        conds.append(BugReport.user_id == f.reporter_id)
    if f.created_from is not None:
        conds.append(BugReport.created_at >= f.created_from)
    if f.created_to is not None:
        conds.append(BugReport.created_at < f.created_to)
    return conds


def _view(row: Any) -> BugReportView:
    return BugReportView(*row)


class BugReportStaffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(
        self, f: ListFilter, *, limit: int, offset: int
    ) -> tuple[list[BugReportView], int]:
        where = _where(f)
        rows = await self.session.execute(
            _select(func.left(BugReport.description, PREVIEW_LENGTH))
            .where(*where)
            .order_by(BugReport.created_at.desc(), BugReport.id.desc())
            .limit(limit)
            .offset(offset)
        )
        total = await self.session.scalar(
            select(func.count())
            .select_from(BugReport)
            .join(App, App.id == BugReport.app_id)
            .where(*where)
        )
        return [_view(r) for r in rows.all()], int(total or 0)

    async def get_view(self, report_id: uuid.UUID) -> BugReportView | None:
        row = (
            await self.session.execute(
                _select(BugReport.description)
                .where(BugReport.id == report_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        return None if row is None else _view(row)

    async def get_for_update(self, report_id: uuid.UUID) -> BugReport | None:
        """FOR NO KEY UPDATE: serializes staff writes, does not block FK KEY SHARE locks."""
        return await self.session.scalar(
            select(BugReport)
            .where(BugReport.id == report_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )

    async def apply(self, report_id: uuid.UUID, values: dict[str, Any]) -> None:
        """One UPDATE, so status, handler and `handled_at` change together (CHECKs)."""
        await self.session.execute(
            update(BugReport)
            .where(BugReport.id == report_id)
            .values(**values, updated_at=bumped_updated_at(BugReport.updated_at))
            .execution_options(synchronize_session=False)
        )

    async def app_key(self, app_id: uuid.UUID) -> str:
        key = await self.session.scalar(select(App.key).where(App.id == app_id))
        assert key is not None  # noqa: S101 (FK RESTRICT)
        return key
