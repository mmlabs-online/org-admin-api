"""ADM-040 migration (`911eb99ddf8b`): `usage_limit_overrides` and its downgrade."""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "58a494f0449e"
TABLE = text("SELECT to_regclass('public.usage_limit_overrides') IS NOT NULL")


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


async def _table_exists() -> bool:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            return bool(await conn.scalar(TABLE))
    finally:
        await engine.dispose()


def test_downgrade_removes_table() -> None:
    cfg = _cfg()
    try:
        command.downgrade(cfg, PREVIOUS)
        assert asyncio.run(_table_exists()) is False
        command.upgrade(cfg, "head")
        assert asyncio.run(_table_exists()) is True
    finally:
        command.upgrade(cfg, "head")
