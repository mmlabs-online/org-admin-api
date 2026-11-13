"""Register-app CLI (USR-07): create/update, refusals, exit codes, lock order, no leaks."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import (
    MSG_ADMIN_APP,
    MSG_ADMIN_PERMISSION,
    MSG_APP_DISABLED,
    MSG_BAD_ORIGIN,
    MSG_BAD_PERMISSION,
    MSG_CONFLICT,
    MSG_CORS_NOTE,
    MSG_DB_ECHO,
    MSG_DRY_RUN,
    MSG_LOOPBACK,
    MSG_ORG_MODE,
    MSG_ORIGIN_TAKEN,
    MSG_REGISTER_UNEXPECTED,
    REGISTER_USAGE,
    amain,
)
from app.core.config import Settings
from app.core.errors import ConflictError
from app.modules.rbac.models import App, Permission, Role, RolePermission
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.schemas import AppRegister
from app.modules.rbac.service import AppService
from tests.rbac_factories import make_app, make_permission, make_role

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD = "https://ricotdin.mmlabs.online"
STAGING = "https://ricotdin-staging.mmlabs.online"
Scope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _scope(db_session: AsyncSession) -> Scope:
    @asynccontextmanager
    async def _s() -> AsyncIterator[AsyncSession]:
        yield db_session  # the fixture owns the transaction

    return _s


def _savepoint_scope(db_session: AsyncSession) -> Scope:
    @asynccontextmanager
    async def _s() -> AsyncIterator[AsyncSession]:
        async with db_session.begin_nested():
            yield db_session

    return _s


@asynccontextmanager
async def _never() -> AsyncIterator[AsyncSession]:
    raise AssertionError("session scope must not be entered")
    yield  # pragma: no cover


def _assert_no_leak(out: str, err: str, *values: str) -> None:
    both = out + err
    assert "Traceback" not in both
    assert "postgresql" not in both
    for value in values:
        assert value not in both


async def _run(db_session: AsyncSession, *argv: str, settings: Settings | None = None) -> int:
    return await amain(
        ["register-app", *argv],
        settings=settings or Settings(),
        session_scope=_scope(db_session),
    )


def _ricotdin(*extra: str, origin: str = PROD, name: str = "Ricotdin") -> list[str]:
    return ["--key", "ricotdin", "--name", name, "--origin", origin, *extra]


async def _app(session: AsyncSession, key: str = "ricotdin") -> App | None:
    return await session.scalar(
        select(App).where(App.key == key).execution_options(populate_existing=True)
    )


async def _member_keys(session: AsyncSession, app: App) -> list[str]:
    role = await session.scalar(
        select(Role).where(Role.app_id == app.id, Role.key.like("%:member"))
    )
    assert role is not None
    rows = await session.scalars(
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role.id)
    )
    return sorted(rows)


async def test_create_app_and_member_role(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, *_ricotdin("--join-policy", "open", "--org-mode", "none")) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "created app ricotdin",
        "name: Ricotdin",
        "join_policy: open",
        "org_mode: none",
        f"origins: {PROD}",
        "member role: ricotdin:member = (none)",
        f"+ origin {PROD}",
        MSG_CORS_NOTE,
    ]
    assert err == ""
    app = await _app(db_session)
    assert app is not None
    assert (app.name, app.join_policy, app.org_mode, app.status) == (
        "Ricotdin",
        "open",
        "none",
        "active",
    )
    assert app.origins == [PROD]
    role = await db_session.get(Role, app.default_role_id)
    assert role is not None
    assert role.key == "ricotdin:member"
    assert await _member_keys(db_session, app) == []
    _assert_no_leak(out, err)


async def test_create_two_separate_apps(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, *_ricotdin()) == 0
    staging = ["--key", "ricotdin-staging", "--name", "Ricotdin Staging", "--origin", STAGING]
    assert await _run(db_session, *staging) == 0
    capsys.readouterr()
    prod, stage = await _app(db_session), await _app(db_session, "ricotdin-staging")
    assert prod is not None and stage is not None
    assert prod.origins == [PROD]
    assert stage.origins == [STAGING]
    prod_role = await db_session.get(Role, prod.default_role_id)
    stage_role = await db_session.get(Role, stage.default_role_id)
    assert prod_role is not None and stage_role is not None
    assert prod_role.key == "ricotdin:member"
    assert stage_role.key == "ricotdin-staging:member"


async def test_rerun_unchanged_no_write(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, *_ricotdin()) == 0
    app = await _app(db_session)
    assert app is not None
    before = app.updated_at
    capsys.readouterr()
    assert await _run(db_session, *_ricotdin()) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "unchanged app ricotdin",
        "name: Ricotdin",
        "join_policy: open",
        "org_mode: none",
        f"origins: {PROD}",
        "member role: ricotdin:member = (none)",
    ]
    app = await _app(db_session)
    assert app is not None
    assert app.updated_at == before
    _assert_no_leak(out, err)


async def test_origins_replaced_with_diff(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    other = "https://ricotdin2.mmlabs.online"
    assert await _run(db_session, *_ricotdin("--origin", other)) == 0
    capsys.readouterr()
    assert await _run(db_session, *_ricotdin(origin=STAGING)) == 0
    out, _ = capsys.readouterr()
    lines = out.splitlines()
    assert lines[0] == "updated app ricotdin"
    assert lines[6:] == [
        f"+ origin {STAGING}",
        f"- origin {PROD}",
        f"- origin {other}",
        MSG_CORS_NOTE,
    ]
    app = await _app(db_session)
    assert app is not None
    assert app.origins == [STAGING]

    assert await _run(db_session, *_ricotdin("--origin", other, origin=STAGING)) == 0
    capsys.readouterr()
    assert await _run(db_session, *_ricotdin("--origin", STAGING, origin=other)) == 0
    out, _ = capsys.readouterr()
    assert out.splitlines()[0] == "unchanged app ricotdin"
    app = await _app(db_session)
    assert app is not None
    assert app.origins == [STAGING, other]


async def test_policy_kept_when_flag_omitted(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, *_ricotdin("--join-policy", "approval")) == 0
    capsys.readouterr()
    assert await _run(db_session, *_ricotdin()) == 0
    out, _ = capsys.readouterr()
    assert out.splitlines()[:3] == [
        "unchanged app ricotdin",
        "name: Ricotdin",
        "join_policy: approval",
    ]
    app = await _app(db_session)
    assert app is not None
    assert app.join_policy == "approval"


async def test_join_policy_changed_when_passed(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, *_ricotdin("--join-policy", "approval")) == 0
    capsys.readouterr()
    assert await _run(db_session, *_ricotdin("--join-policy", "open")) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[0] == "updated app ricotdin"
    assert lines[6:] == ["~ join_policy: approval -> open"]
    app = await _app(db_session)
    assert app is not None
    assert app.join_policy == "open"
    _assert_no_leak(out, err)


async def test_org_mode_change_refused(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, *_ricotdin()) == 0
    capsys.readouterr()
    assert await _run(db_session, *_ricotdin("--org-mode", "required", name="Renamed")) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ORG_MODE + "\n"
    app = await _app(db_session)
    assert app is not None
    assert (app.org_mode, app.name) == ("none", "Ricotdin")


async def test_same_org_mode_accepted(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, *_ricotdin()) == 0
    capsys.readouterr()
    assert await _run(db_session, *_ricotdin("--org-mode", "none")) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "unchanged app ricotdin"
    assert err == ""


async def test_disabled_app_refused_exit_3(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await make_app(db_session, "ricotdin", status="disabled", origins=[STAGING])
    assert await _run(db_session, *_ricotdin()) == 3
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_APP_DISABLED + "\n"
    app = await _app(db_session)
    assert app is not None
    assert (app.status, app.origins) == ("disabled", [STAGING])


async def test_status_and_default_role_untouched(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    app = await make_app(db_session, "ricotdin", origins=[PROD])
    custom = await make_role(db_session, app, "ricotdin:custom")
    app.default_role_id = custom.id
    await db_session.flush()
    assert await _run(db_session, *_ricotdin(name="Ricotdin 2")) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[0] == "updated app ricotdin"
    assert lines[5] == "member role: missing"
    assert lines[6:] == ["~ name: Ricotdin -> Ricotdin 2"]
    reloaded = await _app(db_session)
    assert reloaded is not None
    assert (reloaded.default_role_id, reloaded.status) == (custom.id, "active")
    _assert_no_leak(out, err)


async def test_null_default_role_kept_after_permission_add(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """USR-07 S1: a NULL default (staff cleared it) is not set back by a rebuild."""
    await make_app(db_session, "ricotdin", origins=[PROD])
    assert await _run(db_session, *_ricotdin("--permission", "ricotdin:app:use")) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "updated app ricotdin"
    app = await _app(db_session)
    assert app is not None
    assert app.default_role_id is None
    assert await _member_keys(db_session, app) == ["ricotdin:app:use"]
    _assert_no_leak(out, err)


async def test_admin_refused_exit_64(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["register-app", "--key", "admin", "--name", "A", "--origin", PROD]
    assert await amain(argv, settings=Settings(), session_scope=_never) == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ADMIN_APP + "\n"


async def test_service_refuses_admin(db_session: AsyncSession) -> None:
    admin = await _app(db_session, "admin")
    assert admin is not None
    before = (admin.name, list(admin.origins), admin.updated_at)
    data = AppRegister(key="admin", name="Hijack", origins=["https://admin.mmlabs.online"])
    with pytest.raises(ConflictError) as info:
        await AppService(db_session).register(data, app_env="test")
    assert info.value.details == [{"field": "key", "reason": "admin_app"}]
    admin = await _app(db_session, "admin")
    assert admin is not None
    assert (admin.name, list(admin.origins), admin.updated_at) == before


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "https://api.mmlabs.online",
        "https://x.mmlabs.online:8443",
        "https://evil.com",
    ],
)
async def test_invalid_origin_exit_64(origin: str, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["register-app", *_ricotdin(origin=origin)]
    assert await amain(argv, settings=Settings(), session_scope=_never) == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_BAD_ORIGIN + "\n"
    _assert_no_leak(out, err, origin)


async def test_loopback_refused_in_production(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings().model_copy(update={"app_env": "production"})
    code = await _run(db_session, *_ricotdin(origin="http://localhost:5173"), settings=settings)
    assert code == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_LOOPBACK + "\n"
    assert await _app(db_session) is None
    _assert_no_leak(out, err, "localhost")


async def test_loopback_allowed_locally(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    local = "http://localhost:5173"
    assert await _run(db_session, *_ricotdin(origin=local)) == 0
    other = ["--key", "ricotdin-staging", "--name", "Staging", "--origin", local]
    assert await _run(db_session, *other) == 0
    capsys.readouterr()
    for key in ("ricotdin", "ricotdin-staging"):
        app = await _app(db_session, key)
        assert app is not None
        assert app.origins == [local]


async def test_origin_taken_exit_1(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await make_app(db_session, "stoktok", origins=[PROD])
    assert await _run(db_session, *_ricotdin()) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ORIGIN_TAKEN + "\n"
    assert await _app(db_session) is None
    _assert_no_leak(out, err, PROD, "stoktok")


async def test_origin_taken_by_admin_app(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    admin = await _app(db_session, "admin")
    assert admin is not None
    admin.origins = ["https://admin.mmlabs.online"]
    await db_session.flush()
    assert await _run(db_session, *_ricotdin(origin="https://admin.mmlabs.online")) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ORIGIN_TAKEN + "\n"
    assert await _app(db_session) is None


async def test_permissions_added_to_member_role(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    perms = ["--permission", "ricotdin:app:use", "--permission", "ricotdin:notes:write"]
    assert await _run(db_session, *_ricotdin(*perms)) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[0] == "created app ricotdin"
    assert lines[5] == "member role: ricotdin:member = ricotdin:app:use, ricotdin:notes:write"
    assert lines[6:] == [
        f"+ origin {PROD}",
        "+ permission ricotdin:app:use",
        "+ permission ricotdin:notes:write",
        "+ member ricotdin:app:use",
        "+ member ricotdin:notes:write",
        MSG_CORS_NOTE,
    ]
    app = await _app(db_session)
    assert app is not None
    rows = list(await db_session.scalars(select(Permission).where(Permission.app_id == app.id)))
    assert sorted(p.key for p in rows) == ["ricotdin:app:use", "ricotdin:notes:write"]
    assert not any(p.is_admin for p in rows)
    assert await _member_keys(db_session, app) == ["ricotdin:app:use", "ricotdin:notes:write"]

    third = [*perms, "--permission", "ricotdin:notes:read"]
    assert await _run(db_session, *_ricotdin(*third)) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[0] == "updated app ricotdin"
    assert lines[6:] == ["+ permission ricotdin:notes:read", "+ member ricotdin:notes:read"]
    _assert_no_leak(out, err)


@pytest.mark.parametrize("perm", ["ricotdin:*", "stoktok:app:use", "ricotdin:x", "RICOTDIN:a:b"])
async def test_permission_rejected_exit_64(
    perm: str, db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, *_ricotdin("--permission", perm)) == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_BAD_PERMISSION + "\n"
    assert await _app(db_session) is None
    count = await db_session.scalar(select(Permission.id).where(Permission.key == perm))
    assert count is None
    _assert_no_leak(out, err)


async def test_existing_admin_permission_refused(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    app = await make_app(db_session, "ricotdin", origins=[PROD])
    await make_permission(db_session, app, "ricotdin:roles:manage", is_admin=True)
    argv = _ricotdin("--permission", "ricotdin:roles:manage", name="Changed")
    assert await _run(db_session, *argv) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ADMIN_PERMISSION + "\n"
    reloaded = await _app(db_session)
    assert reloaded is not None
    assert (reloaded.name, reloaded.default_role_id) == ("Ricotdin", None)


async def test_rerun_without_new_permission_keeps_tightening(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    perms = ["--permission", "ricotdin:app:use", "--permission", "ricotdin:notes:write"]
    assert await _run(db_session, *_ricotdin(*perms)) == 0
    app = await _app(db_session)
    assert app is not None
    removed = await db_session.scalar(
        select(Permission.id).where(Permission.key == "ricotdin:notes:write")
    )
    await db_session.execute(
        delete(RolePermission).where(
            RolePermission.role_id == app.default_role_id, RolePermission.permission_id == removed
        )
    )
    capsys.readouterr()
    assert await _run(db_session, *_ricotdin(*perms)) == 0
    out, _ = capsys.readouterr()
    assert out.splitlines()[0] == "unchanged app ricotdin"
    assert await _member_keys(db_session, app) == ["ricotdin:app:use"]


async def test_register_locks_app_first(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        assert await _run(db_session, *_ricotdin("--permission", "ricotdin:app:use")) == 0
    finally:
        event.remove(conn, "before_cursor_execute", _record)

    lock = next(
        i for i, s in enumerate(statements) if "FROM apps" in s and "FOR NO KEY UPDATE" in s
    )
    writes = [i for i, s in enumerate(statements) if s.startswith(("INSERT", "UPDATE"))]
    assert writes
    assert lock < min(writes)
    capsys.readouterr()


async def test_dry_run_rolls_back(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await amain(
        ["register-app", *_ricotdin("--permission", "ricotdin:app:use", "--dry-run")],
        settings=Settings(),
        session_scope=_savepoint_scope(db_session),
    )
    assert code == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[0] == "created app ricotdin"
    assert "+ member ricotdin:app:use" in lines
    assert lines[-2:] == [MSG_CORS_NOTE, MSG_DRY_RUN]
    assert err == ""
    assert await _app(db_session) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["--key", "ricotdin", "--name", "Ricotdin"],
        ["--key", "ricotdin", "--origin", PROD],
        ["--name", "Ricotdin", "--origin", PROD],
        [*_ricotdin(), "--join-policy", "maybe"],
        [*_ricotdin(), "--org-mode", "x"],
        [*_ricotdin(), "--extra", "secret-value"],
        [*_ricotdin(), "secret-value"],
        [],
    ],
)
async def test_usage_errors_exit_64(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert await amain(["register-app", *argv], settings=Settings(), session_scope=_never) == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == REGISTER_USAGE + "\n"
    _assert_no_leak(out, err, "secret-value", "maybe", "Ricotdin")


async def test_db_echo_refused_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = await amain(
        ["register-app", *_ricotdin()], settings=Settings(db_echo=True), session_scope=_never
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_DB_ECHO + "\n"


async def test_db_error_exit_1_no_leak(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def _boom(*_: Any) -> None:
        raise DBAPIError(
            "SELECT apps.id FROM apps WHERE apps.key = $1",
            {"origins": [PROD]},
            Exception(f"origin {PROD} postgresql://u:pw@host/db"),
        )

    monkeypatch.setattr(RbacRepository, "lock_app", _boom)
    assert await _run(db_session, *_ricotdin()) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_REGISTER_UNEXPECTED + "\n"
    _assert_no_leak(out, err, PROD, "u:pw")


async def test_concurrent_create_conflict_exit_1(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def _dup(*_: Any) -> None:
        raise IntegrityError("INSERT INTO apps", {}, Exception("duplicate key"))

    monkeypatch.setattr(RbacRepository, "add_app", _dup)
    assert await _run(db_session, *_ricotdin()) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_CONFLICT + "\n"
    _assert_no_leak(out, err, PROD)


def test_subprocess_register_usage_exit_64() -> None:
    env = {k: v for k, v in os.environ.items() if k not in {"DB_ECHO", "APP_VERSION"}}
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "register-app", "--key", "x"],
        cwd=REPO_ROOT,
        env=env | {"APP_ENV": "test"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 64
    assert proc.stdout == ""
    assert proc.stderr == REGISTER_USAGE + "\n"
