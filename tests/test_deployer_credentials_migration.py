"""ADM-035 migration (`5b7d1e9c3a42`): constraints, SET NULL and the downgrade.

Plain `def` command tests (`migrations/env.py` calls `asyncio.run`); they always end at head.
"""

import asyncio
import hashlib
import uuid
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.conftest import TEST_DATABASE_URL
from tests.rbac_factories import make_user

PREVIOUS = "89fd8d667a25"
HASH = hashlib.sha256(b"adm035-migration").digest()


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


def test_downgrade_drops_table() -> None:
    cfg = _cfg()
    table = (text("SELECT to_regclass('deployer_credentials')"), {})
    try:
        command.downgrade(cfg, PREVIOUS)
        (gone,) = asyncio.run(_run(table))
        assert gone == [(None,)]
        command.upgrade(cfg, "head")
        (back,) = asyncio.run(_run(table))
        assert back != [(None,)]
    finally:
        command.upgrade(cfg, "head")


async def _insert(
    db: AsyncSession, key_id: str, digest: bytes = HASH, created_by: uuid.UUID | None = None
) -> None:
    await db.execute(
        text(
            "INSERT INTO deployer_credentials (id, key_id, secret_hash, created_by) "
            "VALUES (gen_random_uuid(), :key_id, :secret_hash, :created_by)"
        ),
        {"key_id": key_id, "secret_hash": digest, "created_by": created_by},
    )


async def test_table_and_constraints(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    await _insert(db_session, "0123456789abcdef", created_by=user.id)

    for key_id, digest in [
        ("0123456789ABCDEF", HASH),  # uppercase hex
        ("0123456789abcde", HASH),  # 15 chars
        ("fedcba9876543210", HASH[:31]),  # hash length
        ("0123456789abcdef", HASH),  # unique key_id
    ]:
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await _insert(db_session, key_id, digest)

    await db_session.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
    created_by = await db_session.scalar(
        text("SELECT created_by FROM deployer_credentials WHERE key_id = '0123456789abcdef'")
    )
    assert created_by is None
