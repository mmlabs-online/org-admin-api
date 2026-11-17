"""USR-06b migration (`c5e91d3a7b24`): downgrade drops the table and the `invite` send-log rows.

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

PREVIOUS = "b4d82f6a1c93"
HMAC = hashlib.sha256(b"usr06b-migration").digest()


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


def _insert(purpose: str) -> tuple[Any, dict[str, Any]]:
    return (
        text(
            "INSERT INTO email_send_log (id, email_hmac, purpose) "
            "VALUES (gen_random_uuid(), :h, :p)"
        ),
        {"h": HMAC, "p": purpose},
    )


def test_upgrade_downgrade_roundtrip_drops_invite_send_rows() -> None:
    cfg = _cfg()
    table = (text("SELECT to_regclass('org_invitations')"), {})
    try:
        asyncio.run(_run(_insert("invite"), _insert("verify")))
        command.downgrade(cfg, PREVIOUS)
        (exists, rows) = asyncio.run(
            _run(
                table,
                (text("SELECT purpose FROM email_send_log WHERE email_hmac = :h"), {"h": HMAC}),
            )
        )
        assert exists == [(None,)]  # the table is gone
        assert rows == [("verify",)]  # the invite row went with the widened CHECK
        command.upgrade(cfg, "head")
        (exists_again,) = asyncio.run(_run(table))
        assert exists_again != [(None,)]
        asyncio.run(_run(_insert("invite")))  # accepted again
    finally:
        asyncio.run(_run((text("DELETE FROM email_send_log WHERE email_hmac = :h"), {"h": HMAC})))
        command.upgrade(cfg, "head")
