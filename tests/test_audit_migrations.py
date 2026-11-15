"""Audit migrations (ADM-004c): the purge function's down/up cycle and `af3eb84d2d5e`'s guard.

Command tests are plain `def`: `migrations/env.py` calls `asyncio.run`. They always end at head.
"""

import asyncio
import importlib.util
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.conftest import TEST_DATABASE_URL

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
ORIGINAL_BODY = " BEGIN RAISE EXCEPTION 'audit_log is append-only'; END "
PROSRC = text("SELECT prosrc FROM pg_proc WHERE proname = 'audit_log_immutable'")
TRIGGERS = text(
    "SELECT tgname FROM pg_trigger WHERE tgrelid = 'audit_log'::regclass AND NOT tgisinternal"
)


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


def _load(name: str) -> ModuleType:
    (path,) = VERSIONS.glob(f"*_{name}_*.py")
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _fetch(*queries: Any) -> list[Any]:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            return [list((await conn.execute(q)).scalars()) for q in queries]
    finally:
        await engine.dispose()


async def _flagged_delete_refused() -> None:
    """With the flag on, DELETE of a 91-day-old row raises (nothing committed)."""
    engine = create_async_engine(TEST_DATABASE_URL)
    row_id = uuid.uuid4()
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "INSERT INTO audit_log (id, actor_kind, action, occurred_at) VALUES (:id,"
                    " 'anonymous', 'auth.login_failed', now() - interval '91 days')"
                ),
                {"id": row_id},
            )
            await conn.execute(text("SET LOCAL audit.allow_purge = 'on'"))
            with pytest.raises(DBAPIError, match="audit_log is append-only"):
                await conn.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": row_id})
            await conn.rollback()
    finally:
        await engine.dispose()


def test_purge_migration_down_restores_original_function() -> None:
    cfg = _cfg()
    try:
        command.downgrade(cfg, "af3eb84d2d5e")
        ((prosrc,),) = asyncio.run(_fetch(PROSRC))
        assert prosrc == ORIGINAL_BODY
        asyncio.run(_flagged_delete_refused())
    finally:
        command.upgrade(cfg, "head")


def test_purge_migration_up_down_up() -> None:
    cfg = _cfg()
    try:
        command.downgrade(cfg, "-1")
    finally:
        command.upgrade(cfg, "head")
    (prosrc,), triggers = asyncio.run(_fetch(PROSRC, TRIGGERS))
    assert "allow_purge" in prosrc
    assert set(triggers) == {"audit_log_no_update_delete", "audit_log_no_truncate"}


async def test_outcome_downgrade_refuses_with_failure_rows(db_session: AsyncSession) -> None:
    await db_session.execute(
        text(
            "INSERT INTO audit_log (id, actor_kind, action, outcome) "
            "VALUES (gen_random_uuid(), 'anonymous', 'auth.login_failed', 'denied')"
        )
    )
    guard = _load("af3eb84d2d5e")._refuse_if_outcome_rows
    conn = await db_session.connection()
    with pytest.raises(RuntimeError, match="failure/denied rows"):
        await conn.run_sync(guard)


class _CleanBind:
    def scalar(self, _: Any) -> bool:
        return False


def test_outcome_downgrade_passes_on_clean_table() -> None:
    # A real empty table cannot be assumed: the concurrency tests commit failure rows.
    _load("af3eb84d2d5e")._refuse_if_outcome_rows(_CleanBind())


def test_outcome_downgrade_refuses_offline() -> None:
    with pytest.raises(RuntimeError, match="offline"):
        command.downgrade(_cfg(), "af3eb84d2d5e:c3f1a9d27b60", sql=True)
