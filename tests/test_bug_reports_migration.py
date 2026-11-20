"""ADM-028 migration (`b3f7a9c1d452`): `bug_reports` down and up."""

import asyncio
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "d9e4b2a61f73"


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = url
    cfg.attributes["allow_outcome_loss"] = True
    cfg.attributes["allow_audit_log_loss"] = True
    return cfg


async def _table_exists(url: str) -> bool:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            return await conn.scalar(text("SELECT to_regclass('public.bug_reports')")) is not None
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


def test_bug_reports_down_up() -> None:
    # Throwaway database, like the other DDL tests: the shared test DB stays untouched.
    name = f"adm028_{uuid.uuid4().hex[:12]}"
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
