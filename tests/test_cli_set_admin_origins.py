"""Set-admin-origins CLI (ADM-002c): replace, refusals, invariants, lock order, no leaks."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import (
    MSG_ADMIN_APEX,
    MSG_ADMIN_BAD_ORIGIN,
    MSG_ADMIN_CLOSED,
    MSG_ADMIN_NOT_SEEDED,
    MSG_ADMIN_TOO_MANY,
    MSG_CONFLICT,
    MSG_CORS_NOTE,
    MSG_DB_ECHO,
    MSG_DRY_RUN,
    MSG_LOOPBACK,
    MSG_ORIGIN_TAKEN,
    MSG_SET_ORIGINS_UNEXPECTED,
    SET_ADMIN_ORIGINS_USAGE,
    amain,
)
from app.core.config import Settings
from app.core.errors import ConflictError, UnprocessableError
from app.modules.rbac.models import App, Permission, Role, RoleAssignment, RolePermission
from app.modules.rbac.repository import APPS_CHANGED_KEY, RbacRepository
from app.modules.rbac.schemas import AdminOriginsSet
from app.modules.rbac.service import AdminOriginsOutcome, AppService
from tests.rbac_factories import make_app, super_admin_role

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN = "https://admin.mmlabs.online"
ADMIN2 = "https://admin2.mmlabs.online"
LOCAL = "http://localhost:3000"
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


def _prod() -> Settings:
    return Settings().model_copy(update={"app_env": "production"})


async def _run(db_session: AsyncSession, *argv: str, settings: Settings | None = None) -> int:
    return await amain(
        ["set-admin-origins", *argv],
        settings=settings or Settings(),
        session_scope=_scope(db_session),
    )


async def _admin(session: AsyncSession) -> App:
    app = await session.scalar(
        select(App).where(App.key == "admin").execution_options(populate_existing=True)
    )
    assert app is not None
    return app


async def _set(session: AsyncSession, *origins: str) -> None:
    admin = await _admin(session)
    admin.origins = list(origins)
    await session.flush()


async def _service_set(session: AsyncSession, *origins: str, app_env: str = "test") -> Any:
    return await AppService(session).set_admin_origins(
        AdminOriginsSet(origins=list(origins)), app_env=app_env
    )


# --- service ---------------------------------------------------------------------------------


async def test_service_sets_origins(db_session: AsyncSession) -> None:
    await _set(db_session)
    result = await _service_set(db_session, ADMIN)
    assert result.outcome == AdminOriginsOutcome.UPDATED
    assert result.origins == (ADMIN,)
    assert result.origins_added == (ADMIN,)
    assert result.origins_removed == ()
    assert db_session.info[APPS_CHANGED_KEY] is True
    assert (await _admin(db_session)).origins == [ADMIN]


async def test_service_replaces_with_diff(db_session: AsyncSession) -> None:
    await _set(db_session, ADMIN)
    result = await _service_set(db_session, ADMIN2)
    assert result.outcome == AdminOriginsOutcome.UPDATED
    assert (result.origins_added, result.origins_removed) == ((ADMIN2,), (ADMIN,))
    assert (await _admin(db_session)).origins == [ADMIN2]


async def _snapshot(session: AsyncSession) -> tuple[Any, ...]:
    admin = await _admin(session)
    roles = sorted(
        (r.key, r.name, r.is_system)
        for r in await session.scalars(select(Role).where(Role.app_id == admin.id))
    )
    perms = sorted(
        (p.key, p.is_admin, p.description)
        for p in await session.scalars(select(Permission).where(Permission.app_id == admin.id))
    )
    role_perms = sorted(
        (str(rp.role_id), str(rp.permission_id))
        for rp in await session.scalars(
            select(RolePermission).where(RolePermission.app_id == admin.id)
        )
    )
    assignments = await session.scalar(
        select(func.count())
        .select_from(RoleAssignment)
        .join(Role, Role.id == RoleAssignment.role_id)
        .where(Role.app_id == admin.id)
    )
    return (
        admin.key,
        admin.name,
        admin.status,
        admin.join_policy,
        admin.org_mode,
        admin.default_role_id,
        roles,
        perms,
        role_perms,
        assignments,
    )


async def test_service_invariants_unchanged(db_session: AsyncSession) -> None:
    await _set(db_session)
    before = await _snapshot(db_session)
    result = await _service_set(db_session, ADMIN, ADMIN2)
    assert result.outcome == AdminOriginsOutcome.UPDATED
    assert await _snapshot(db_session) == before
    assert (await _admin(db_session)).origins == [ADMIN, ADMIN2]


async def test_service_rerun_unchanged_no_write(db_session: AsyncSession) -> None:
    await _set(db_session, ADMIN, ADMIN2)
    before = (await _admin(db_session)).updated_at
    db_session.info.pop(APPS_CHANGED_KEY, None)
    result = await _service_set(db_session, ADMIN2, "https://ADMIN.mmlabs.online/")
    assert result.outcome == AdminOriginsOutcome.UNCHANGED
    assert (result.origins_added, result.origins_removed) == ((), ())
    admin = await _admin(db_session)
    assert admin.updated_at == before
    assert admin.origins == [ADMIN, ADMIN2]
    assert APPS_CHANGED_KEY not in db_session.info


async def test_service_clear(db_session: AsyncSession) -> None:
    await _set(db_session, ADMIN)
    result = await _service_set(db_session)
    assert result.outcome == AdminOriginsOutcome.UPDATED
    assert (result.origins, result.origins_removed) == ((), (ADMIN,))
    assert (await _admin(db_session)).origins == []


async def test_service_clear_when_empty_unchanged(db_session: AsyncSession) -> None:
    await _set(db_session)
    before = (await _admin(db_session)).updated_at
    db_session.info.pop(APPS_CHANGED_KEY, None)
    result = await _service_set(db_session)
    assert result.outcome == AdminOriginsOutcome.UNCHANGED
    assert (await _admin(db_session)).updated_at == before
    assert APPS_CHANGED_KEY not in db_session.info


@pytest.mark.parametrize("app_env", ["staging", "production"])
async def test_service_loopback_refused(app_env: str, db_session: AsyncSession) -> None:
    await _set(db_session)
    with pytest.raises(UnprocessableError) as info:
        await _service_set(db_session, LOCAL, app_env=app_env)
    assert info.value.details == [{"field": "origins", "reason": "loopback_not_allowed"}]
    assert (await _admin(db_session)).origins == []


async def test_service_loopback_allowed_locally(db_session: AsyncSession) -> None:
    await _set(db_session)
    result = await _service_set(db_session, LOCAL, app_env="local")
    assert result.outcome == AdminOriginsOutcome.UPDATED
    assert (await _admin(db_session)).origins == [LOCAL]


@pytest.mark.parametrize("apex", ["https://mmlabs.online", "https://MMLABS.online/"])
async def test_service_apex_refused(apex: str, db_session: AsyncSession) -> None:
    await _set(db_session)
    with pytest.raises(UnprocessableError) as info:
        await _service_set(db_session, apex)
    assert info.value.details == [{"field": "origins", "reason": "apex_not_allowed"}]
    assert (await _admin(db_session)).origins == []


async def test_service_third_origin_refused(db_session: AsyncSession) -> None:
    await _set(db_session)
    with pytest.raises(UnprocessableError) as info:
        await _service_set(db_session, ADMIN, ADMIN2, "https://admin3.mmlabs.online")
    assert info.value.details == [{"field": "origins", "reason": "too_many_origins"}]
    assert (await _admin(db_session)).origins == []


async def test_service_duplicates_count_once(db_session: AsyncSession) -> None:
    await _set(db_session)
    result = await _service_set(db_session, ADMIN, ADMIN + "/", ADMIN2)
    assert result.outcome == AdminOriginsOutcome.UPDATED
    assert (await _admin(db_session)).origins == [ADMIN, ADMIN2]


@pytest.mark.parametrize("status", ["active", "disabled"])
async def test_service_origin_taken(status: str, db_session: AsyncSession) -> None:
    await _set(db_session, ADMIN2)
    await make_app(db_session, "ricotdin", status=status, origins=[ADMIN])
    with pytest.raises(ConflictError) as info:
        await _service_set(db_session, ADMIN)
    assert info.value.details == [{"field": "origins", "reason": "origin_taken"}]
    assert (await _admin(db_session)).origins == [ADMIN2]


async def test_service_loopback_not_checked_for_taken(db_session: AsyncSession) -> None:
    await _set(db_session)
    await make_app(db_session, "ricotdin", origins=[LOCAL])
    result = await _service_set(db_session, LOCAL, app_env="local")
    assert result.outcome == AdminOriginsOutcome.UPDATED
    assert (await _admin(db_session)).origins == [LOCAL]


@pytest.mark.parametrize(
    "field", ["status_disabled", "join_open", "org_required", "default_role_set"]
)
async def test_service_refuses_drift(field: str, db_session: AsyncSession) -> None:
    admin = await _admin(db_session)
    admin.origins = []
    if field == "status_disabled":
        admin.status = "disabled"
    elif field == "join_open":
        admin.join_policy = "open"
    elif field == "org_required":
        admin.org_mode = "required"
    else:
        admin.default_role_id = (await super_admin_role(db_session)).id
    await db_session.flush()
    drifted = (admin.status, admin.join_policy, admin.org_mode, admin.default_role_id)

    with pytest.raises(ConflictError) as info:
        await _service_set(db_session, ADMIN)
    assert info.value.details == [{"field": "key", "reason": "admin_not_seeded"}]
    admin = await _admin(db_session)
    assert admin.origins == []
    assert (admin.status, admin.join_policy, admin.org_mode, admin.default_role_id) == drifted


async def test_service_refuses_missing_admin(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _none(*_: Any) -> None:
        return None

    monkeypatch.setattr(RbacRepository, "lock_app", _none)
    with pytest.raises(ConflictError) as info:
        await _service_set(db_session, ADMIN)
    assert info.value.details == [{"field": "key", "reason": "admin_not_seeded"}]


async def test_service_locks_admin_first(db_session: AsyncSession) -> None:
    await _set(db_session)
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        await _service_set(db_session, ADMIN)
    finally:
        event.remove(conn, "before_cursor_execute", _record)

    on_apps = [s for s in statements if "apps" in s]
    assert on_apps
    assert "FROM apps" in on_apps[0]
    assert "FOR NO KEY UPDATE" in on_apps[0]
    assert any(s.startswith("UPDATE apps") for s in on_apps)


# --- CLI -------------------------------------------------------------------------------------


async def test_cli_sets_origins_exit_0(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _set(db_session)
    assert await _run(db_session, "--origin", ADMIN) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "updated app admin",
        "join_policy: closed",
        "status: active",
        f"origins: {ADMIN}",
        f"+ origin {ADMIN}",
        MSG_CORS_NOTE,
    ]
    assert err == ""
    assert (await _admin(db_session)).origins == [ADMIN]


async def test_cli_rerun_prints_unchanged(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _set(db_session, ADMIN)
    assert await _run(db_session, "--origin", ADMIN) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "unchanged app admin",
        "join_policy: closed",
        "status: active",
        f"origins: {ADMIN}",
    ]
    assert err == ""


async def test_cli_clear_exit_0(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _set(db_session, ADMIN)
    assert await _run(db_session, "--clear") == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "updated app admin",
        "join_policy: closed",
        "status: active",
        "origins: (none)",
        f"- origin {ADMIN}",
        MSG_CORS_NOTE,
        MSG_ADMIN_CLOSED,
    ]
    assert err == ""
    assert (await _admin(db_session)).origins == []


async def test_cli_dry_run_rolls_back(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _set(db_session)
    before = (await _admin(db_session)).updated_at
    code = await amain(
        ["set-admin-origins", "--origin", ADMIN, "--dry-run"],
        settings=Settings(),
        session_scope=_savepoint_scope(db_session),
    )
    assert code == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[0] == "updated app admin"
    assert f"+ origin {ADMIN}" in lines
    assert lines[-2:] == [MSG_CORS_NOTE, MSG_DRY_RUN]
    assert err == ""
    admin = await _admin(db_session)
    assert (admin.origins, admin.updated_at) == ([], before)


async def test_cli_clear_dry_run_rolls_back(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _set(db_session, ADMIN)
    code = await amain(
        ["set-admin-origins", "--clear", "--dry-run"],
        settings=Settings(),
        session_scope=_savepoint_scope(db_session),
    )
    assert code == 0
    out, _ = capsys.readouterr()
    assert out.splitlines()[-1] == MSG_DRY_RUN
    assert (await _admin(db_session)).origins == [ADMIN]


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="no_args"),
        pytest.param(["--clear", "--origin", ADMIN], id="clear+origin"),
        pytest.param(["--key", "admin", "--origin", ADMIN], id="key_admin"),
        pytest.param(["--orig", ADMIN], id="abbrev_orig"),
        pytest.param(["--origin", ADMIN, "--extra", "secret-value"], id="unknown_flag"),
        pytest.param(["--origin"], id="origin_without_value"),
    ],
)
async def test_cli_usage_errors_exit_64(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = await amain(["set-admin-origins", *argv], settings=Settings(), session_scope=_never)
    assert code == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == SET_ADMIN_ORIGINS_USAGE + "\n"
    _assert_no_leak(out, err, "secret-value", "admin.mmlabs")


@pytest.mark.parametrize(
    "origin",
    [
        pytest.param("http://admin.mmlabs.online", id="http"),
        pytest.param("https://api.mmlabs.online", id="api_host"),
        pytest.param("https://evil.example", id="foreign"),
        pytest.param("https://admin.mmlabs.online:8443", id="port"),
        pytest.param("https://10.0.0.1", id="ip"),
        pytest.param("", id="empty"),
    ],
)
async def test_cli_invalid_origin_exit_64(origin: str, capsys: pytest.CaptureFixture[str]) -> None:
    code = await amain(
        ["set-admin-origins", "--origin", origin], settings=Settings(), session_scope=_never
    )
    assert code == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ADMIN_BAD_ORIGIN + "\n"
    if origin:
        _assert_no_leak(out, err, origin)


async def test_cli_loopback_production_exit_64(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _set(db_session)
    assert await _run(db_session, "--origin", LOCAL, settings=_prod()) == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_LOOPBACK + "\n"
    assert (await _admin(db_session)).origins == []


async def test_cli_apex_exit_64(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, "--origin", "https://mmlabs.online") == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ADMIN_APEX + "\n"


async def test_cli_third_origin_exit_64(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--origin", ADMIN, "--origin", ADMIN2, "--origin", "https://admin3.mmlabs.online"]
    assert await _run(db_session, *argv) == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ADMIN_TOO_MANY + "\n"


async def test_cli_origin_taken_exit_1(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _set(db_session)
    await make_app(db_session, "ricotdin", origins=[ADMIN])
    assert await _run(db_session, "--origin", ADMIN) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ORIGIN_TAKEN + "\n"
    assert (await _admin(db_session)).origins == []


async def test_cli_drift_exit_1(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    admin = await _admin(db_session)
    admin.join_policy = "open"
    await db_session.flush()
    assert await _run(db_session, "--origin", ADMIN) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_ADMIN_NOT_SEEDED + "\n"


@pytest.mark.parametrize(
    ("origins", "settings", "taken", "drift"),
    [
        pytest.param(["http://localhost:4321"], "production", False, False, id="loopback"),
        pytest.param(["https://MMLABS.online/"], "test", False, False, id="apex"),
        pytest.param(
            [
                "https://adm1.mmlabs.online",
                "https://adm2.mmlabs.online",
                "https://adm3.mmlabs.online",
            ],
            "test",
            False,
            False,
            id="too_many",
        ),
        pytest.param(["https://adm1.mmlabs.online"], "test", True, False, id="taken"),
        pytest.param(["https://adm1.mmlabs.online"], "test", False, True, id="drift"),
        pytest.param(["https://adm1.mmlabs.online:9"], "test", False, False, id="invalid"),
    ],
)
async def test_cli_refusals_never_echo_values(
    origins: list[str],
    settings: str,
    taken: bool,
    drift: bool,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _set(db_session)
    if taken:
        await make_app(db_session, "ricotdin", origins=origins)
    if drift:
        admin = await _admin(db_session)
        admin.org_mode = "required"
        await db_session.flush()
    argv = [a for o in origins for a in ("--origin", o)]
    env = _prod() if settings == "production" else Settings()
    assert await _run(db_session, *argv, settings=env) in (1, 64)
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("error: ")
    assert err.count("\n") == 1
    _assert_no_leak(out, err, *origins, "ricotdin")  # the fixed apex message names the apex


async def test_cli_db_echo_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = await amain(
        ["set-admin-origins", "--origin", ADMIN],
        settings=Settings(db_echo=True),
        session_scope=_never,
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_DB_ECHO + "\n"


async def test_cli_db_error_exit_1_no_leak(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def _boom(*_: Any) -> None:
        raise DBAPIError(
            "SELECT apps.id FROM apps WHERE apps.key = $1",
            {"origins": [ADMIN]},
            Exception(f"origin {ADMIN} postgresql://u:pw@host/db"),
        )

    monkeypatch.setattr(RbacRepository, "lock_app", _boom)
    assert await _run(db_session, "--origin", ADMIN) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_SET_ORIGINS_UNEXPECTED + "\n"
    _assert_no_leak(out, err, ADMIN, "u:pw")


async def test_cli_conflict_exit_1(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def _race(*_: Any) -> None:
        raise IntegrityError("UPDATE apps", {}, Exception("conflict"))

    await _set(db_session)
    monkeypatch.setattr(RbacRepository, "save", _race)
    assert await _run(db_session, "--origin", ADMIN) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_CONFLICT + "\n"
    _assert_no_leak(out, err, ADMIN)


def test_subprocess_set_admin_origins_usage_exit_64() -> None:
    env = {k: v for k, v in os.environ.items() if k not in {"DB_ECHO", "APP_VERSION"}}
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "set-admin-origins"],
        cwd=REPO_ROOT,
        env=env | {"APP_ENV": "test"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 64
    assert proc.stdout == ""
    assert proc.stderr == SET_ADMIN_ORIGINS_USAGE + "\n"
