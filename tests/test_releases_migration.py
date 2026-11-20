"""ADM-033 migration: `app_releases` down and up."""

import asyncio
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "c4e8a2f7b916"


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = url
    cfg.attributes["allow_outcome_loss"] = True
    cfg.attributes["allow_audit_log_loss"] = True
    return cfg


async def _constraints(url: str) -> set[str] | None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            if await conn.scalar(text("SELECT to_regclass('public.app_releases')")) is None:
                return None
            rows = await conn.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'app_releases'::regclass")
            )
            return {r[0] for r in rows}
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


def test_app_releases_down_up() -> None:
    name = f"adm033_{uuid.uuid4().hex[:12]}"
    url = make_url(TEST_DATABASE_URL).set(database=name).render_as_string(hide_password=False)
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        cfg = _cfg(url)
        command.upgrade(cfg, "head")
        names = asyncio.run(_constraints(url))
        assert names is not None
        assert {
            "pk_app_releases",
            "fk_app_releases_app_id_apps",
            "uq_app_releases_app_id_environment_version",
            "ck_app_releases_version_matches_environment",
            "ck_app_releases_deployed_iff_deployed_at",
        } <= names
        command.downgrade(cfg, PREVIOUS)
        assert asyncio.run(_constraints(url)) is None
        command.upgrade(cfg, "head")
        assert asyncio.run(_constraints(url)) == names
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
