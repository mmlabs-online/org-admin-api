"""USR-06c migration (`d6f03e8b2a15`): scope backfill, reversible, preflight aborts.

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

PREVIOUS = "c5e91d3a7b24"
CHECKS = text(
    "SELECT conname FROM pg_constraint WHERE conname IN "
    "('ck_permissions_scope_valid', 'ck_apps_default_role_not_org_owner')"
)
SCOPE_COLUMN = text(
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_name = 'permissions' AND column_name = 'scope'"
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


def _seed(
    app_key: str, org_mode: str, *, default_owner_same: bool = False
) -> tuple[list[tuple[Any, dict[str, Any]]], dict[str, uuid.UUID]]:
    """An app with a `member` (default) and an `org-owner` role and six permissions.

    org:create (both roles), org:invite (both), data:dflt (member), data:own (owner),
    data:both (both), data:none (neither).
    """
    app_id, member, owner = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ids = {k: uuid.uuid4() for k in ("create", "invite", "dflt", "own", "both", "none")}
    keys = {
        "create": "org:create",
        "invite": "org:invite",
        "dflt": "data:dflt",
        "own": "data:own",
        "both": "data:both",
        "none": "data:none",
    }
    held = {
        "member": ["create", "dflt", "both", "invite"],
        "owner": ["create", "invite", "own", "both"],
    }
    stmts: list[tuple[Any, dict[str, Any]]] = [
        (
            text("INSERT INTO apps (id, key, name, org_mode) VALUES (:i, :k, :k, :m)"),
            {"i": app_id, "k": app_key, "m": org_mode},
        ),
        (
            text("INSERT INTO roles (id, app_id, key, name) VALUES (:i, :a, :k, 'r')"),
            {"i": member, "a": app_id, "k": f"{app_key}:member"},
        ),
        (
            text("INSERT INTO roles (id, app_id, key, name) VALUES (:i, :a, :k, 'r')"),
            {"i": owner, "a": app_id, "k": f"{app_key}:org-owner"},
        ),
    ]
    for name, pid in ids.items():
        stmts.append(
            (
                text("INSERT INTO permissions (id, app_id, key) VALUES (:i, :a, :k)"),
                {"i": pid, "a": app_id, "k": f"{app_key}:{keys[name]}"},
            )
        )
    for role_name, role_id in (("member", member), ("owner", owner)):
        for name in held[role_name]:
            stmts.append(
                (
                    text(
                        "INSERT INTO role_permissions (role_id, permission_id, app_id) "
                        "VALUES (:r, :p, :a)"
                    ),
                    {"r": role_id, "p": ids[name], "a": app_id},
                )
            )
    owner_param = member if default_owner_same else owner
    stmts.append(
        (
            text("UPDATE apps SET default_role_id = :d, org_owner_role_id = :o WHERE id = :i"),
            {"d": member, "o": owner_param if org_mode == "required" else None, "i": app_id},
        )
    )
    return stmts, {"app": app_id, **ids}


async def _cleanup(*app_ids: uuid.UUID) -> None:
    for app_id in app_ids:
        await _run(
            (
                text(
                    "UPDATE apps SET default_role_id = NULL, org_owner_role_id = NULL WHERE id = :i"
                ),
                {"i": app_id},
            ),
            (text("DELETE FROM apps WHERE id = :i"), {"i": app_id}),
        )


async def _scopes(app_id: uuid.UUID) -> dict[str, str]:
    ((rows),) = await _run(
        (text("SELECT key, scope FROM permissions WHERE app_id = :a"), {"a": app_id})
    )
    return {key.split(":", 1)[1]: scope for key, scope in rows}


def test_backfill_classifies_org_app_permissions() -> None:
    cfg = _cfg()
    stmts, ids = _seed("mgrorg", "required")
    try:
        command.downgrade(cfg, PREVIOUS)
        asyncio.run(_run(*stmts))
        command.upgrade(cfg, "head")
        assert asyncio.run(_scopes(ids["app"])) == {
            "org:create": "app",  # reserved
            "org:invite": "org",  # reserved, even though the default role holds it
            "data:dflt": "app",  # default only
            "data:own": "org",  # owner only
            "data:both": "org",  # both
            "data:none": "org",  # neither: fail closed
        }
    finally:
        asyncio.run(_cleanup(ids["app"]))
        command.upgrade(cfg, "head")


def test_none_app_permissions_stay_app() -> None:
    cfg = _cfg()
    stmts, ids = _seed("mgrnone", "none")
    try:
        command.downgrade(cfg, PREVIOUS)
        asyncio.run(_run(*stmts))
        command.upgrade(cfg, "head")
        assert set(asyncio.run(_scopes(ids["app"])).values()) == {"app"}
    finally:
        asyncio.run(_cleanup(ids["app"]))
        command.upgrade(cfg, "head")


def test_migration_never_touches_role_permissions() -> None:
    cfg = _cfg()
    stmts, ids = _seed("mgrrp", "required")
    count = text("SELECT count(*) FROM role_permissions WHERE app_id = :a")
    try:
        command.downgrade(cfg, PREVIOUS)
        asyncio.run(_run(*stmts))
        (before,) = asyncio.run(_run((count, {"a": ids["app"]})))
        command.upgrade(cfg, "head")
        (after,) = asyncio.run(_run((count, {"a": ids["app"]})))
        assert before == after == [(8,)]
    finally:
        asyncio.run(_cleanup(ids["app"]))
        command.upgrade(cfg, "head")


def test_downgrade_drops_column_and_checks_then_upgrade_recomputes() -> None:
    cfg = _cfg()
    stmts, ids = _seed("mgrdown", "required")
    try:
        command.downgrade(cfg, PREVIOUS)
        asyncio.run(_run(*stmts))
        command.upgrade(cfg, "head")
        command.downgrade(cfg, PREVIOUS)
        column, checks = asyncio.run(_run((SCOPE_COLUMN, {}), (CHECKS, {})))
        assert column == [] and checks == []
        command.upgrade(cfg, "head")
        assert asyncio.run(_scopes(ids["app"]))["data:own"] == "org"
        column, checks = asyncio.run(_run((SCOPE_COLUMN, {}), (CHECKS, {})))
        assert len(column) == 1 and len(checks) == 2
    finally:
        asyncio.run(_cleanup(ids["app"]))
        command.upgrade(cfg, "head")


def test_preflight_aborts_on_default_equals_owner() -> None:
    cfg = _cfg()
    stmts, ids = _seed("mgrbad", "required", default_owner_same=True)
    try:
        command.downgrade(cfg, PREVIOUS)
        asyncio.run(_run(*stmts))
        with pytest.raises(RuntimeError, match="default_role_id = org_owner_role_id"):
            command.upgrade(cfg, "head")
        (row,) = asyncio.run(
            _run(
                (
                    text("SELECT default_role_id = org_owner_role_id FROM apps WHERE id = :i"),
                    {"i": ids["app"]},
                )
            )
        )
        assert row == [(True,)]  # never auto-fixed
    finally:
        asyncio.run(_cleanup(ids["app"]))
        command.upgrade(cfg, "head")
