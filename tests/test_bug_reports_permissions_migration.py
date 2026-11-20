"""ADM-029 migration (`c4e8a2f7b916`): the bug-report permissions and their downgrade."""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "b3f7a9c1d452"
QUERY = text(
    "SELECT key, is_admin, "
    "(SELECT count(*) FROM role_permissions rp WHERE rp.permission_id = p.id) "
    "FROM permissions p WHERE key LIKE 'admin:bug-reports:%' ORDER BY key"
)


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


async def _rows() -> list[tuple[str, bool, int]]:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            return [(r[0], r[1], r[2]) for r in await conn.execute(QUERY)]
    finally:
        await engine.dispose()


def test_downgrade_removes_bug_report_permissions() -> None:
    cfg = _cfg()
    try:
        command.downgrade(cfg, PREVIOUS)
        assert asyncio.run(_rows()) == []
        command.upgrade(cfg, "head")
        assert asyncio.run(_rows()) == [
            ("admin:bug-reports:read", True, 0),
            ("admin:bug-reports:write", True, 0),
        ]
    finally:
        command.upgrade(cfg, "head")
