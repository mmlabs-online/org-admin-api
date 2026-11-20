"""ADM-039 migration (`8c2f6a1d4e93`): the usage tables, permissions and downgrade."""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "5b7d1e9c3a42"
PERMS = text(
    "SELECT key, is_admin, "
    "(SELECT count(*) FROM role_permissions rp WHERE rp.permission_id = p.id) "
    "FROM permissions p WHERE key LIKE 'admin:usage:%' ORDER BY key"
)
TABLE = text("SELECT to_regclass('public.usage_metrics') IS NOT NULL")


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


async def _state() -> tuple[bool, list[tuple[str, bool, int]]]:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            table = bool(await conn.scalar(TABLE))
            return table, [(r[0], r[1], r[2]) for r in await conn.execute(PERMS)]
    finally:
        await engine.dispose()


def test_downgrade_removes_table_and_permissions() -> None:
    cfg = _cfg()
    try:
        command.downgrade(cfg, PREVIOUS)
        assert asyncio.run(_state()) == (False, [])
        command.upgrade(cfg, "head")
        assert asyncio.run(_state()) == (
            True,
            [("admin:usage:read", True, 0), ("admin:usage:write", True, 0)],
        )
    finally:
        command.upgrade(cfg, "head")
