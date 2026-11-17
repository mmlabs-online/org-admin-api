"""ADM-006 migration (`c8a1f4e72d95`): constraints, RESTRICT FKs and the downgrade.

CHECK drift is invisible to `alembic check`, so `test_constraints` guards the CHECKs.
"""

import asyncio
import uuid
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.conftest import TEST_DATABASE_URL
from tests.rbac_factories import make_app, make_org

PREVIOUS = "a9d4c27e6b13"
KEYS = ("admin:entitlements:read", "admin:entitlements:write")


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


def test_downgrade_drops_table_and_permissions() -> None:
    cfg = _cfg()
    table = (text("SELECT to_regclass('org_entitlements')"), {})
    perms = (
        text("SELECT count(*) FROM permissions WHERE key IN :keys").bindparams(
            sa_bindparam("keys", expanding=True)
        ),
        {"keys": list(KEYS)},
    )
    try:
        command.downgrade(cfg, PREVIOUS)
        gone, no_perms = asyncio.run(_run(table, perms))
        assert gone == [(None,)]
        assert no_perms == [(0,)]
        command.upgrade(cfg, "head")
        back, two_perms = asyncio.run(_run(table, perms))
        assert back != [(None,)]
        assert two_perms == [(2,)]
    finally:
        command.upgrade(cfg, "head")


async def _insert(
    db: AsyncSession, org_id: uuid.UUID, app_id: uuid.UUID, plan: str = "pro"
) -> None:
    await db.execute(
        text(
            "INSERT INTO org_entitlements (id, org_id, app_id, plan) "
            "VALUES (gen_random_uuid(), :o, :a, :p)"
        ),
        {"o": org_id, "a": app_id, "p": plan},
    )


async def test_constraints(db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    app = await make_app(db_session, f"app-{uuid.uuid4().hex[:8]}")
    await _insert(db_session, org.id, app.id)

    with pytest.raises(IntegrityError):  # unique (org, app)
        async with db_session.begin_nested():
            await _insert(db_session, org.id, app.id)
    other = await make_app(db_session, f"app-{uuid.uuid4().hex[:8]}")
    for bad in ("", "Pro", "-pro"):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await _insert(db_session, org.id, other.id, bad)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(text("UPDATE org_entitlements SET limits = '[]'::jsonb"))

    limits, enabled = (
        await db_session.execute(text("SELECT limits, enabled FROM org_entitlements"))
    ).one()
    assert (limits, enabled) == ({}, True)

    for table, row_id in (("organizations", org.id), ("apps", app.id)):
        with pytest.raises(IntegrityError):  # RESTRICT
            async with db_session.begin_nested():
                await db_session.execute(
                    text(f"DELETE FROM {table} WHERE id = :i"),  # noqa: S608 (fixed names)
                    {"i": row_id},
                )
