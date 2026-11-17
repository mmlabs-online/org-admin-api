"""USR-06a migration (`b4d82f6a1c93`): data survives, downgrade reverses, orphans abort.

Command tests are plain `def` (`migrations/env.py` calls `asyncio.run`) and always end at head.
Rows they commit are removed in `finally`.
"""

import asyncio
import uuid
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "a7c3e5f19b42"
FKS = text(
    "SELECT conname FROM pg_constraint WHERE conname IN ("
    "'fk_role_assignments_org_id_organizations', 'fk_organizations_created_by_users',"
    " 'fk_apps_org_owner_role_same_app', 'ck_apps_org_owner_requires_org_mode')"
)
COLUMNS = text(
    "SELECT table_name || '.' || column_name FROM information_schema.columns WHERE "
    "(table_name, column_name) IN (('organizations', 'created_by'), ('apps', 'org_owner_role_id'))"
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


def _insert_rows(
    org_id: uuid.UUID, user_id: uuid.UUID, assignment_id: uuid.UUID, *, org: bool
) -> list[tuple[Any, dict[str, Any]]]:
    statements: list[tuple[Any, dict[str, Any]]] = [
        (
            text("INSERT INTO users (id, email, password_hash) VALUES (:u, :e, 'h')"),
            {"u": user_id, "e": f"{user_id.hex[:12]}@example.test"},
        )
    ]
    if org:
        statements.append(
            (
                text("INSERT INTO organizations (id, slug, name) VALUES (:o, :s, 'Mig')"),
                {"o": org_id, "s": f"mig-{org_id.hex[:12]}"},
            )
        )
    statements.append(
        (
            text(
                "INSERT INTO role_assignments (id, user_id, role_id, org_id) "
                "SELECT :a, :u, id, :o FROM roles WHERE key = 'admin:super-admin'"
            ),
            {"a": assignment_id, "u": user_id, "o": org_id},
        )
    )
    return statements


async def _cleanup(org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await _run(
        (text("DELETE FROM role_assignments WHERE user_id = :u"), {"u": user_id}),
        (text("DELETE FROM organizations WHERE id = :o"), {"o": org_id}),
        (text("DELETE FROM users WHERE id = :u"), {"u": user_id}),
    )


def test_upgrade_keeps_existing_org_and_downgrade_reverses() -> None:
    cfg = _cfg()
    org_id, user_id, ra_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        command.downgrade(cfg, PREVIOUS)
        asyncio.run(_run(*_insert_rows(org_id, user_id, ra_id, org=True)))
        command.upgrade(cfg, "head")

        (org_rows, assignment_rows, fks, columns) = asyncio.run(
            _run(
                (text("SELECT created_by, status FROM organizations WHERE id = :o"), {"o": org_id}),
                (text("SELECT org_id FROM role_assignments WHERE id = :a"), {"a": ra_id}),
                (FKS, {}),
                (COLUMNS, {}),
            )
        )
        (org,), (assignment,) = org_rows, assignment_rows
        assert org == (None, "active")  # kept, created_by NULL
        assert assignment == (org_id,)
        assert len(fks) == 4
        assert len(columns) == 2

        command.downgrade(cfg, PREVIOUS)
        fks_down, columns_down, kept = asyncio.run(
            _run(
                (FKS, {}),
                (COLUMNS, {}),
                (text("SELECT 1 FROM organizations WHERE id = :o"), {"o": org_id}),
            )
        )
        assert fks_down == [] and columns_down == []
        assert kept == [(1,)]  # downgrade drops structure, not rows
    finally:
        asyncio.run(_cleanup(org_id, user_id))
        command.upgrade(cfg, "head")


def test_upgrade_fails_on_orphan_org_id() -> None:
    cfg = _cfg()
    org_id, user_id, ra_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        command.downgrade(cfg, PREVIOUS)
        asyncio.run(_run(*_insert_rows(org_id, user_id, ra_id, org=False)))
        with pytest.raises(RuntimeError, match="reference missing organizations"):
            command.upgrade(cfg, "head")
        (row,) = asyncio.run(
            _run((text("SELECT org_id FROM role_assignments WHERE id = :a"), {"a": ra_id}))
        )
        assert row == [(org_id,)]  # never deleted or NULLed
    finally:
        asyncio.run(_cleanup(org_id, user_id))
        command.upgrade(cfg, "head")
