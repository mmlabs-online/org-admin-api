"""USR-15 migration (`c7a3e91f5d28`): `cancelled`, the data step and the lossy downgrade."""

import asyncio
import uuid
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

PREVIOUS = "e2b7a94d1c58"


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    cfg.attributes["allow_outcome_loss"] = True
    cfg.attributes["allow_audit_log_loss"] = True
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


def _seed(user_status: str, row_status: str) -> tuple[Any, ...]:
    """Statements seeding a committed app, user and one join request; returns ids via params."""
    ids = {"app": uuid.uuid4(), "user": uuid.uuid4(), "req": uuid.uuid4()}
    key = f"mig-{uuid.uuid4().hex[:8]}"
    return (
        ids,
        (
            text("INSERT INTO apps (id, key, name) VALUES (:app, :key, 'm')"),
            {"app": ids["app"], "key": key},
        ),
        (
            text(
                "INSERT INTO users (id, email, password_hash, status) "
                "VALUES (:user, :email, 'h', :status)"
            ),
            {"user": ids["user"], "email": f"{key}@x.io", "status": user_status},
        ),
        (
            text(
                "INSERT INTO join_requests (id, app_id, user_id, status, decided_at) "
                "VALUES (:req, :app, :user, :status, "
                "CASE WHEN :decided THEN now() END)"
            ),
            {**ids, "status": row_status, "decided": row_status != "pending"},
        ),
    )


def _cleanup(ids: dict[str, Any]) -> tuple[Any, ...]:
    return (
        (text("DELETE FROM users WHERE id = :user"), ids),
        (text("DELETE FROM apps WHERE id = :app"), ids),
    )


def test_upgrade_cancels_pending_of_disabled_users() -> None:
    cfg = _cfg()
    disabled_ids, *disabled_stmts = _seed("disabled", "pending")
    active_ids, *active_stmts = _seed("active", "pending")
    try:
        command.downgrade(cfg, PREVIOUS)
        asyncio.run(_run(*disabled_stmts, *active_stmts))
        command.upgrade(cfg, "head")
        select = text(
            "SELECT status, decided_at IS NOT NULL, decided_by FROM join_requests WHERE id = :req"
        )
        (d, a) = asyncio.run(_run((select, disabled_ids), (select, active_ids)))
        assert d == [("cancelled", True, None)]
        assert a == [("pending", False, None)]
    finally:
        command.upgrade(cfg, "head")
        asyncio.run(_run(*_cleanup(disabled_ids), *_cleanup(active_ids)))


def test_downgrade_deletes_cancelled_rows() -> None:
    cfg = _cfg()
    cancelled_ids, *cancelled_stmts = _seed("active", "cancelled")
    rejected_ids, *rejected_stmts = _seed("active", "rejected")
    try:
        asyncio.run(_run(*cancelled_stmts, *rejected_stmts))
        command.downgrade(cfg, PREVIOUS)
        select = text("SELECT count(*) FROM join_requests WHERE id = :req")
        (c, r) = asyncio.run(_run((select, cancelled_ids), (select, rejected_ids)))
        assert (c, r) == ([(0,)], [(1,)])
        try:
            asyncio.run(_run(*_seed("active", "cancelled")[1:]))
        except IntegrityError:
            pass
        else:
            raise AssertionError("cancelled must violate the 3-value CHECK")
    finally:
        command.upgrade(cfg, "head")
        asyncio.run(_run(*_cleanup(cancelled_ids), *_cleanup(rejected_ids)))
