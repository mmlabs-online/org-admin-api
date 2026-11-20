"""ADM-030 migration (`d7a3f5b2e801`): `bug_report_attachments` down and up, CHECKs, cascade."""

import asyncio
import uuid
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.bug_reports.models import BugReport, BugReportAttachment
from tests.auth_factories import make_login_app, make_login_user
from tests.conftest import TEST_DATABASE_URL
from tests.test_bug_reports_migration import _admin, _cfg

PREVIOUS = "8c2f6a1d4e93"


async def _table_exists(url: str) -> bool:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            found = await conn.scalar(text("SELECT to_regclass('public.bug_report_attachments')"))
            return found is not None
    finally:
        await engine.dispose()


def test_upgrade_downgrade_roundtrip() -> None:
    name = f"adm030_{uuid.uuid4().hex[:12]}"
    url = make_url(TEST_DATABASE_URL).set(database=name).render_as_string(hide_password=False)
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        cfg = _cfg(url)
        command.upgrade(cfg, "head")
        assert asyncio.run(_table_exists(url))
        command.downgrade(cfg, PREVIOUS)
        assert not asyncio.run(_table_exists(url))
        command.upgrade(cfg, "head")
        assert asyncio.run(_table_exists(url))
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


async def _report(db: AsyncSession) -> BugReport:
    app = await make_login_app(db, "ricotdin")
    user = await make_login_user(db)
    report = BugReport(app_id=app.id, user_id=user.id, description="x")
    db.add(report)
    await db.flush()
    return report


def _row(report: BugReport, **overrides: Any) -> BugReportAttachment:
    fields: dict[str, Any] = {
        "report_id": report.id,
        "drive_file_id": "F" + uuid.uuid4().hex,
        "content_type": "image/png",
        "size_bytes": 100,
        **overrides,
    }
    return BugReportAttachment(**fields)


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_type": "image/svg+xml"},
        {"content_type": "image/jpg"},
        {"size_bytes": 0},
        {"size_bytes": 11},
        {"size_bytes": 5 * 1024 * 1024 + 1},
        {"drive_file_id": "has space"},
        {"drive_file_id": ""},
    ],
    ids=[
        "bad_type",
        "jpg_alias",
        "size_0",
        "size_11",
        "size_over",
        "bad_id_space",
        "empty_id",
    ],
)
async def test_checks_reject(db_session: AsyncSession, overrides: dict[str, Any]) -> None:
    report = await _report(db_session)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_row(report, **overrides))
            await db_session.flush()


async def test_checks_accept_bounds_and_drive_file_id_is_unique(db_session: AsyncSession) -> None:
    report = await _report(db_session)
    db_session.add_all(
        [
            _row(report, size_bytes=12, drive_file_id="a-_Z9"),
            _row(report, size_bytes=5 * 1024 * 1024, content_type="image/webp"),
        ]
    )
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_row(report, drive_file_id="a-_Z9"))
            await db_session.flush()


async def test_report_delete_cascades(db_session: AsyncSession) -> None:
    report = await _report(db_session)
    db_session.add_all([_row(report), _row(report)])
    await db_session.flush()
    await db_session.execute(text("DELETE FROM bug_reports WHERE id = :i"), {"i": report.id})
    left = await db_session.scalar(
        text("SELECT count(*) FROM bug_report_attachments WHERE report_id = :i"), {"i": report.id}
    )
    assert left == 0
