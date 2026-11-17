"""ADM-015 migration (`e2b7a94d1c58`): the `admin:stats:read` permission and its downgrade."""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "c8a1f4e72d95"
QUERY = text(
    "SELECT is_admin, (SELECT count(*) FROM role_permissions rp WHERE rp.permission_id = p.id) "
    "FROM permissions p WHERE key = 'admin:stats:read'"
)


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


async def _rows() -> list[tuple[bool, int]]:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            return [(r[0], r[1]) for r in await conn.execute(QUERY)]
    finally:
        await engine.dispose()


def test_downgrade_removes_stats_permission() -> None:
    cfg = _cfg()
    try:
        command.downgrade(cfg, PREVIOUS)
        assert asyncio.run(_rows()) == []
        command.upgrade(cfg, "head")
        assert asyncio.run(_rows()) == [(True, 0)]
    finally:
        command.upgrade(cfg, "head")
