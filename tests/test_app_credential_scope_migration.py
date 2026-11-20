"""ADM-034 migration (`58a494f0449e`): the scope column, its CHECK, the index swap, the downgrade.

Plain `def` command tests (`migrations/env.py` calls `asyncio.run`); they always end at head.
"""

import asyncio
import hashlib
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "d7a3f5b2e801"
HASH = hashlib.sha256(b"adm034-migration").digest()


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


_INDEXES = text("SELECT indexname FROM pg_indexes WHERE tablename = 'app_credentials'")
_CHECKS = text(
    "SELECT conname FROM pg_constraint WHERE conrelid = 'app_credentials'::regclass "
    "AND contype = 'c'"
)
_SCOPES = text("SELECT key_id, scope FROM app_credentials ORDER BY key_id")


def test_existing_rows_become_manifest_and_down_up() -> None:
    cfg = _cfg()
    k_old, k_rel = "a" * 16, "b" * 16
    insert = text(
        "INSERT INTO app_credentials (id, app_id, key_id, secret_hash) "
        "SELECT gen_random_uuid(), id, :k, :h FROM apps WHERE key = 'admin'"
    )
    cleanup = text("DELETE FROM app_credentials WHERE key_id IN (:a, :b)")
    try:
        command.downgrade(cfg, PREVIOUS)
        indexes, checks = asyncio.run(_run((_INDEXES, {}), (_CHECKS, {})))
        assert ("ix_app_credentials_app_id_active",) in indexes
        assert ("ix_app_credentials_app_id_scope_active",) not in indexes
        assert ("ck_app_credentials_scope_valid",) not in checks
        asyncio.run(_run((insert, {"k": k_old, "h": HASH})))

        command.upgrade(cfg, "head")
        asyncio.run(
            _run(
                (
                    text(
                        "INSERT INTO app_credentials (id, app_id, key_id, secret_hash, scope) "
                        "SELECT gen_random_uuid(), id, :k, :h, 'releases' "
                        "FROM apps WHERE key = 'admin'"
                    ),
                    {"k": k_rel, "h": HASH},
                )
            )
        )
        scopes, indexes, checks = asyncio.run(_run((_SCOPES, {}), (_INDEXES, {}), (_CHECKS, {})))
        assert dict(scopes) == {k_old: "manifest", k_rel: "releases"}
        assert ("ix_app_credentials_app_id_scope_active",) in indexes
        assert ("ix_app_credentials_app_id_active",) not in indexes
        assert ("ck_app_credentials_scope_valid",) in checks

        # Downgrade deletes the releases key (no silent escalation) and keeps the manifest key.
        command.downgrade(cfg, PREVIOUS)
        (rows,) = asyncio.run(_run((text("SELECT key_id FROM app_credentials"), {})))
        assert rows == [(k_old,)]
        command.upgrade(cfg, "head")
        (scopes,) = asyncio.run(_run((_SCOPES, {})))
        assert dict(scopes) == {k_old: "manifest"}
    finally:
        command.upgrade(cfg, "head")
        asyncio.run(_run((cleanup, {"a": k_old, "b": k_rel})))


def test_scope_check_rejects_unknown_scope() -> None:
    bad = text(
        "INSERT INTO app_credentials (id, app_id, key_id, secret_hash, scope) "
        "SELECT gen_random_uuid(), id, :k, :h, 'usage:read' FROM apps WHERE key = 'admin'"
    )
    try:
        asyncio.run(_run((bad, {"k": "c" * 16, "h": HASH})))
    except Exception as exc:
        assert "ck_app_credentials_scope_valid" in str(exc)
    else:
        raise AssertionError("the CHECK accepted an unknown scope")
