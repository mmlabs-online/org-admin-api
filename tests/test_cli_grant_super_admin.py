"""Bootstrap super-admin CLI (USR-02f): exit codes, idempotency, lock order, no email leak."""

import ast
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import (
    MSG_CONFIG,
    MSG_DB_ECHO,
    MSG_DISABLED,
    MSG_NOT_SEEDED,
    MSG_UNEXPECTED,
    MSG_UNKNOWN_EMAIL,
    USAGE,
    amain,
)
from app.core.config import Settings
from app.core.text import mask_email
from app.modules.rbac.models import RoleAssignment
from app.modules.rbac.repository import RbacRepository
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository
from tests.rbac_factories import assign, super_admin_role

REPO_ROOT = Path(__file__).resolve().parent.parent
EMAIL = "alice.x@example.com"
Scope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _scope(db_session: AsyncSession) -> Scope:
    @asynccontextmanager
    async def _s() -> AsyncIterator[AsyncSession]:
        yield db_session  # the fixture owns the transaction

    return _s


@asynccontextmanager
async def _never() -> AsyncIterator[AsyncSession]:
    raise AssertionError("session scope must not be entered")
    yield  # pragma: no cover


def _assert_no_leak(out: str, err: str, email: str = EMAIL) -> None:
    both = out + err
    assert email.lower() not in both.lower()
    assert "Traceback" not in both
    assert "postgresql" not in both


async def _make_user(
    session: AsyncSession, email: str = EMAIL, *, status: UserStatus = UserStatus.ACTIVE
) -> User:
    user = User(email=email, password_hash="h", status=status.value)
    session.add(user)
    await session.flush()
    return user


async def _rows(session: AsyncSession, user: User) -> list[RoleAssignment]:
    role = await super_admin_role(session)
    result = await session.scalars(
        select(RoleAssignment)
        .where(RoleAssignment.user_id == user.id, RoleAssignment.role_id == role.id)
        .execution_options(populate_existing=True)
    )
    return list(result)


async def _run(db_session: AsyncSession, *argv: str) -> int:
    return await amain(list(argv), settings=Settings(), session_scope=_scope(db_session))


async def test_grant_creates_app_scope_row(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    user = await _make_user(db_session)
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "granted admin:super-admin"
    assert err == ""
    rows = await _rows(db_session, user)
    assert len(rows) == 1
    assert rows[0].org_id is None
    assert rows[0].granted_by is None
    assert rows[0].expires_at is None
    _assert_no_leak(out, err)


async def test_grant_output_fields(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    user = await _make_user(db_session)
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "granted admin:super-admin",
        f"user_id: {user.id}",
        f"email: {mask_email(user.email)}",
        f"created_at: {user.created_at.isoformat()}",
        "email_verified: no",
        "check: created_at must match your own sign-up; if not, revoke this grant (docs/deploy.md)",
    ]
    _assert_no_leak(out, err)

    user.email_verified_at = datetime.now(UTC)
    await db_session.flush()
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "already held admin:super-admin"
    assert "email_verified: yes" in out.splitlines()
    _assert_no_leak(out, err)


async def test_grant_mixed_case_email(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    user = await _make_user(db_session)
    assert await _run(db_session, "grant-super-admin", "--email", "  Alice.X@EXAMPLE.com ") == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "granted admin:super-admin"
    assert len(await _rows(db_session, user)) == 1
    _assert_no_leak(out, err)


async def test_grant_twice_already_held(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    user = await _make_user(db_session)
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 0
    first = await _rows(db_session, user)
    capsys.readouterr()
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "already held admin:super-admin"
    rows = await _rows(db_session, user)
    assert [r.id for r in rows] == [first[0].id]
    _assert_no_leak(out, err)


async def test_grant_replaces_expired_row(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    user = await _make_user(db_session)
    role = await super_admin_role(db_session)
    expired = await assign(db_session, user, role, expires_at=datetime.now(UTC) - timedelta(days=1))
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "granted admin:super-admin"
    rows = await _rows(db_session, user)
    assert [r.id for r in rows] == [expired.id]
    assert rows[0].expires_at is None
    _assert_no_leak(out, err)


async def test_grant_active_row_with_future_expiry_already_held(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    user = await _make_user(db_session)
    role = await super_admin_role(db_session)
    future = datetime.now(UTC) + timedelta(days=1)
    row = await assign(db_session, user, role, expires_at=future)
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "already held admin:super-admin"
    rows = await _rows(db_session, user)
    assert [r.id for r in rows] == [row.id]
    assert rows[0].expires_at == future
    _assert_no_leak(out, err)


async def test_unknown_email_exit_2(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_UNKNOWN_EMAIL + "\n"
    role = await super_admin_role(db_session)
    count = await db_session.scalar(
        select(func.count()).select_from(RoleAssignment).where(RoleAssignment.role_id == role.id)
    )
    assert count == 0
    _assert_no_leak(out, err)


async def test_disabled_user_exit_3_no_write(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    user = await _make_user(db_session, status=UserStatus.DISABLED)
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 3
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_DISABLED + "\n"
    assert await _rows(db_session, user) == []
    _assert_no_leak(out, err)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["grant-super-admin"],
        ["grant-super-admin", "secret@example.com"],
        ["grant-super-admin", "--email", "not-an-email"],
        ["other-cmd"],
        ["grant-super-admin", "--email", "a@b.test", "--extra"],
    ],
)
async def test_usage_errors_exit_64(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert await amain(argv, settings=Settings(), session_scope=_never) == 64
    out, err = capsys.readouterr()
    assert out == ""
    assert err == USAGE + "\n"
    for value in argv:
        if value not in {"grant-super-admin", "--email"}:
            assert value not in err
    _assert_no_leak(out, err, "secret@example.com")


async def test_db_echo_refused_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = await amain(
        ["grant-super-admin", "--email", EMAIL],
        settings=Settings(db_echo=True),
        session_scope=_never,
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_DB_ECHO + "\n"
    _assert_no_leak(out, err)


async def test_settings_error_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _broken() -> Settings:
        raise ValueError("TRUSTED_PROXY_CIDRS must be set postgresql://u:pw@host/db")

    monkeypatch.setattr("app.core.config.get_settings", _broken)
    code = await amain(["grant-super-admin", "--email", EMAIL], session_scope=_never)
    assert code == 1
    out, err = capsys.readouterr()
    assert err == MSG_CONFIG + "\n"
    assert "TRUSTED_PROXY_CIDRS must" not in err
    _assert_no_leak(out, err)


async def test_not_seeded_exit_1(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    await _make_user(db_session)

    async def _missing(*_: Any) -> None:
        return None

    monkeypatch.setattr(RbacRepository, "get_role_by_key", _missing)
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_NOT_SEEDED + "\n"
    _assert_no_leak(out, err)


async def test_db_error_exit_1_no_leak(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def _boom(*_: Any) -> None:
        raise DBAPIError(
            "SELECT users.id FROM users WHERE users.email = $1",
            {"email": EMAIL},
            Exception(f"Key (email)=({EMAIL}) postgresql://u:pw@host/db"),
        )

    monkeypatch.setattr(UserRepository, "get_by_email_for_share", _boom)
    assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_UNEXPECTED + "\n"
    _assert_no_leak(out, err)


async def test_grant_with_rbac_flag_off(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings()
    assert settings.admin_rbac_api_enabled is False
    assert settings.admin_users_api_enabled is False
    await _make_user(db_session)
    code = await amain(
        ["grant-super-admin", "--email", EMAIL],
        settings=settings,
        session_scope=_scope(db_session),
    )
    assert code == 0
    out, err = capsys.readouterr()
    _assert_no_leak(out, err)


async def test_grant_locks_admin_app_then_user(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _make_user(db_session)
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        assert await _run(db_session, "grant-super-admin", "--email", EMAIL) == 0
    finally:
        event.remove(conn, "before_cursor_execute", _record)

    def _index(pred: Callable[[str], bool]) -> int:
        return next(i for i, s in enumerate(statements) if pred(s))

    app_lock = _index(lambda s: "FROM apps" in s and "FOR NO KEY UPDATE" in s)
    user_lock = _index(lambda s: "FROM users" in s and "FOR SHARE" in s)
    insert = _index(lambda s: s.startswith("INSERT INTO role_assignments"))
    assert app_lock < user_lock < insert
    out, err = capsys.readouterr()
    _assert_no_leak(out, err)


def test_cli_not_imported_by_app() -> None:
    for path in (REPO_ROOT / "app").rglob("*.py"):
        if path == REPO_ROOT / "app" / "cli.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                assert all(a.name != "app.cli" for a in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "app.cli", path
                if node.module == "app":
                    assert all(a.name != "cli" for a in node.names), path


def _subprocess_env(**overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"DB_ECHO", "APP_VERSION"}}
    return env | {"APP_ENV": "test"} | overrides


def test_subprocess_usage_exit_64() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "grant-super-admin", "secret@example.com"],
        cwd=REPO_ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 64
    assert proc.stderr == USAGE + "\n"
    _assert_no_leak(proc.stdout, proc.stderr, "secret@example.com")


def test_subprocess_unreachable_db_exit_1() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "grant-super-admin",
            "--email",
            "Secret.Person@example.com",
        ],
        cwd=REPO_ROOT,
        env=_subprocess_env(DATABASE_URL="postgresql+asyncpg://u:s3cr3t-pw@127.0.0.1:1/x"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr == MSG_UNEXPECTED + "\n"
    both = proc.stdout + proc.stderr
    assert "s3cr3t-pw" not in both
    assert "127.0.0.1" not in both
    _assert_no_leak(proc.stdout, proc.stderr, "Secret.Person@example.com")
