"""RBAC data migrations (ADM-002d): the `a7c3e5f19b42` backfill of `is_admin` on admin-app
permissions.

Command tests are plain `def`: `migrations/env.py` calls `asyncio.run`. They always end at head.
"""

import asyncio
import uuid
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

ADMIN_PERM = "admin:zz-backfill:read"
OTHER_APP = "zz-backfill"
OTHER_PERM = f"{OTHER_APP}:items:read"


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


async def _run(*statements: tuple[str, dict[str, Any]]) -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            for sql, params in statements:
                await conn.execute(text(sql), params)
    finally:
        await engine.dispose()


async def _is_admin(key: str) -> bool:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            query = text("SELECT is_admin FROM permissions WHERE key = :k")
            return bool((await conn.execute(query, {"k": key})).scalar_one())
    finally:
        await engine.dispose()


def _seed() -> None:
    app_id = uuid.uuid4()
    asyncio.run(
        _run(
            (
                "INSERT INTO apps (id, key, name) VALUES (:id, :key, 'Backfill test')",
                {"id": app_id, "key": OTHER_APP},
            ),
            (
                "INSERT INTO permissions (id, app_id, key, is_admin) VALUES "
                "(:id, (SELECT id FROM apps WHERE key = 'admin'), :key, false)",
                {"id": uuid.uuid4(), "key": ADMIN_PERM},
            ),
            (
                "INSERT INTO permissions (id, app_id, key, is_admin) "
                "VALUES (:id, :app, :key, false)",
                {"id": uuid.uuid4(), "app": app_id, "key": OTHER_PERM},
            ),
        )
    )


def _cleanup() -> None:
    asyncio.run(
        _run(
            ("DELETE FROM permissions WHERE key = :key", {"key": ADMIN_PERM}),
            ("DELETE FROM apps WHERE key = :key", {"key": OTHER_APP}),  # cascades OTHER_PERM
        )
    )


def test_admin_permissions_backfill() -> None:
    cfg = _cfg()
    try:
        command.downgrade(cfg, "e91b6c3d58a2")
        _seed()
        command.upgrade(cfg, "head")
        assert asyncio.run(_is_admin(ADMIN_PERM)) is True
        assert asyncio.run(_is_admin(OTHER_PERM)) is False
    finally:
        _cleanup()
        command.upgrade(cfg, "head")
