"""ADM-042 migration (`c91e4b7a3d58`): both tables, up and down."""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "b7d3f1a9c542"
TABLES = text(
    "SELECT to_regclass('public.usage_counters') IS NOT NULL, "
    "to_regclass('public.usage_events') IS NOT NULL"
)


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


async def _tables() -> tuple[bool, bool]:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            row = (await conn.execute(TABLES)).one()
            return bool(row[0]), bool(row[1])
    finally:
        await engine.dispose()


def test_tables_round_trip() -> None:
    cfg = _cfg()
    try:
        command.downgrade(cfg, PREVIOUS)
        assert asyncio.run(_tables()) == (False, False)
        command.upgrade(cfg, "head")
        assert asyncio.run(_tables()) == (True, True)
    finally:
        command.upgrade(cfg, "head")
