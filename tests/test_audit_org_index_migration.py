"""ADM-021 migration (`d9e4b2a61f73`): `ix_audit_log_org_id` down and up."""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "c7a3e91f5d28"


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    cfg.attributes["allow_outcome_loss"] = True
    cfg.attributes["allow_audit_log_loss"] = True
    return cfg


async def _indexdef() -> str | None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            return await conn.scalar(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_audit_log_org_id'")
            )
    finally:
        await engine.dispose()


def test_org_index_down_up() -> None:
    cfg = _cfg()
    try:
        command.downgrade(cfg, PREVIOUS)
        assert asyncio.run(_indexdef()) is None
        command.upgrade(cfg, "head")
        definition = asyncio.run(_indexdef())
        assert definition is not None
        assert "WHERE (org_id IS NOT NULL)" in definition
    finally:
        command.upgrade(cfg, "head")
