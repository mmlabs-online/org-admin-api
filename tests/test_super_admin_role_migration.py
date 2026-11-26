"""USR-38 migration (`e7b2c4f9a316`): `apps.super_admin_role_id` goes away on downgrade and comes
back NULL on upgrade.

The command test is a plain `def` (`migrations/env.py` calls `asyncio.run`) and always ends at
head. Rows it commits are removed in `finally`.
"""

import asyncio
import uuid
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "d4a8e2c6f175"
APP_KEY = "zz-usr38"
COLUMN = text(
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_name = 'apps' AND column_name = 'super_admin_role_id'"
)


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


async def _run(*statements: tuple[Any, dict[str, Any]]) -> list[list[Any]]:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            out: list[list[Any]] = []
            for q, p in statements:
                result = await conn.execute(q, p)
                out.append(list(result.all()) if result.returns_rows else [])
            return out
    finally:
        await engine.dispose()


def test_upgrade_downgrade_upgrade() -> None:
    cfg = _cfg()
    app_id, role_id = uuid.uuid4(), uuid.uuid4()
    try:
        asyncio.run(
            _run(
                (
                    text(
                        "INSERT INTO apps (id, key, name, org_mode) "
                        "VALUES (:a, :k, 'Zz', 'required')"
                    ),
                    {"a": app_id, "k": APP_KEY},
                ),
                (
                    text("INSERT INTO roles (id, app_id, key, name) VALUES (:r, :a, :rk, 'R')"),
                    {"r": role_id, "a": app_id, "rk": f"{APP_KEY}:root"},
                ),
                (
                    text("UPDATE apps SET super_admin_role_id = :r WHERE id = :a"),
                    {"r": role_id, "a": app_id},
                ),
            )
        )
        command.downgrade(cfg, PREVIOUS)
        assert asyncio.run(_run((COLUMN, {})))[0][0][0] == 0
        command.upgrade(cfg, "head")
        assert asyncio.run(_run((COLUMN, {})))[0][0][0] == 1
        value = asyncio.run(
            _run((text("SELECT super_admin_role_id FROM apps WHERE id = :a"), {"a": app_id}))
        )[0][0][0]
        assert value is None
    finally:
        asyncio.run(_run((text("DELETE FROM apps WHERE key = :k"), {"k": APP_KEY})))
        command.upgrade(cfg, "head")
