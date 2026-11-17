"""USR-03g migration (`a9d4c27e6b13`): constraints, cascades and the downgrade.

CHECK drift is invisible to `alembic check`, so `test_constraints` guards the CHECKs.
"""

import asyncio
import uuid
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.conftest import TEST_DATABASE_URL
from tests.rbac_factories import make_app, make_user

PREVIOUS = "f3a8d51c9e27"


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
    table = (text("SELECT to_regclass('join_requests')"), {})
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
    db: AsyncSession,
    app_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str,
    decided: bool = False,
    decided_by: uuid.UUID | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO join_requests (id, app_id, user_id, status, decided_at, decided_by) "
            "VALUES (gen_random_uuid(), :app_id, :user_id, :status, "
            "CASE WHEN :decided THEN now() END, :decided_by)"
        ),
        {
            "app_id": app_id,
            "user_id": user_id,
            "status": status,
            "decided": decided,
            "decided_by": decided_by,
        },
    )


async def test_constraints(db_session: AsyncSession) -> None:
    app = await make_app(db_session, f"app-{uuid.uuid4().hex[:8]}")
    user, staff = await make_user(db_session), await make_user(db_session)
    await _insert(db_session, app.id, user.id, "pending")

    for status, decided in [
        ("approved", False),  # decided without decided_at
        ("pending", True),  # pending with decided_at
        ("bogus", True),
    ]:
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await _insert(db_session, app.id, user.id, status, decided)
    with pytest.raises(IntegrityError):  # a second pending row for the same (app, user)
        async with db_session.begin_nested():
            await _insert(db_session, app.id, user.id, "pending")

    await db_session.execute(
        text("UPDATE join_requests SET status = 'rejected', decided_at = now(), decided_by = :s"),
        {"s": staff.id},
    )
    await _insert(db_session, app.id, user.id, "pending")  # allowed after the first is decided

    # decided_by is SET NULL on staff delete; the requests cascade with the user.
    await db_session.execute(text("DELETE FROM users WHERE id = :i"), {"i": staff.id})
    decided_by = await db_session.scalar(
        text("SELECT decided_by FROM join_requests WHERE status = 'rejected'")
    )
    assert decided_by is None
    await db_session.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
    left = await db_session.scalar(
        text("SELECT count(*) FROM join_requests WHERE app_id = :i"), {"i": app.id}
    )
    assert left == 0
