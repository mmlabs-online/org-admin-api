"""ADM-038 migration (`b7d3f1a9c542`): round trip, CHECKs, default."""

import asyncio
import uuid
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.bug_reports.models import BugReport
from tests.auth_factories import make_login_app, make_login_user
from tests.conftest import TEST_DATABASE_URL
from tests.release_factories import make_release
from tests.test_bug_reports_migration import _admin, _cfg

PREVIOUS = "a4c81e6d27f3"


async def _has_columns(url: str) -> bool:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            n = await conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_name = "
                    "'bug_reports' AND column_name IN ('release_id', 'release_match')"
                )
            )
            return n == 2
    finally:
        await engine.dispose()


def test_upgrade_downgrade_roundtrip() -> None:
    name = f"adm038_{uuid.uuid4().hex[:12]}"
    url = make_url(TEST_DATABASE_URL).set(database=name).render_as_string(hide_password=False)
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        cfg = _cfg(url)
        command.upgrade(cfg, "head")
        assert asyncio.run(_has_columns(url))
        command.downgrade(cfg, PREVIOUS)
        assert not asyncio.run(_has_columns(url))
        command.upgrade(cfg, "head")
        assert asyncio.run(_has_columns(url))
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


async def _report(db: AsyncSession, **kw: Any) -> BugReport:
    app = await make_login_app(db, "ricotdin")
    user = await make_login_user(db)
    report = BugReport(app_id=app.id, user_id=user.id, description="x", **kw)
    db.add(report)
    await db.flush()
    return report


async def test_existing_rows_default_none(db_session: AsyncSession) -> None:
    report = await _report(db_session)
    await db_session.refresh(report)
    assert (report.release_id, report.release_match) == (None, "none")


async def test_checks_reject_bad_release_match_and_linked_none(db_session: AsyncSession) -> None:
    app = await make_login_app(db_session, "ricotdin")
    rel = await make_release(db_session, app)
    user = await make_login_user(db_session)
    for kw in ({"release_match": "x"}, {"release_id": rel.id, "release_match": "none"}):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                db_session.add(BugReport(app_id=app.id, user_id=user.id, description="x", **kw))
                await db_session.flush()
