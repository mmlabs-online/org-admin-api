"""ADM-021 migration (`d9e4b2a61f73`): `ix_audit_log_org_id` down and up."""

import asyncio
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "c7a3e91f5d28"


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = url
    cfg.attributes["allow_outcome_loss"] = True
    cfg.attributes["allow_audit_log_loss"] = True
    return cfg


async def _indexdef(url: str) -> str | None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            return await conn.scalar(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_audit_log_org_id'")
            )
    finally:
        await engine.dispose()


async def _admin(statement: str) -> None:
    engine = create_async_engine(
        make_url(TEST_DATABASE_URL).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text(statement))
    finally:
        await engine.dispose()


def test_org_index_down_up() -> None:
    # Throwaway database: DDL here must not touch the shared test DB's audit_log statistics
    # (it flips the planner in the EXPLAIN-based tests).
    name = f"adm021_{uuid.uuid4().hex[:12]}"
    url = make_url(TEST_DATABASE_URL).set(database=name).render_as_string(hide_password=False)
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        cfg = _cfg(url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, PREVIOUS)
        assert asyncio.run(_indexdef(url)) is None
        command.upgrade(cfg, "head")
        definition = asyncio.run(_indexdef(url))
        assert definition is not None
        assert "WHERE (org_id IS NOT NULL)" in definition
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
