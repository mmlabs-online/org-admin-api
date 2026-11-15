"""create-account CLI (USR-09, ADR-0009): verified user, join, refusals, secrecy, one txn."""

import getpass
import io
import logging
import os
import signal
import subprocess
import sys
import time
import warnings
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from select import select as fd_select
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import (
    CREATE_USAGE,
    MSG_ABORTED,
    MSG_CREATE_UNEXPECTED,
    MSG_DB_ECHO,
    MSG_EMAIL_MISMATCH,
    MSG_EMAIL_TAKEN,
    MSG_JOIN_ADMIN,
    MSG_JOIN_APPROVAL,
    MSG_JOIN_CLOSED,
    MSG_JOIN_UNAVAILABLE,
    MSG_JOIN_UNKNOWN,
    MSG_NO_TTY,
    MSG_PASSWORD_MISMATCH,
    MSG_WEAK_PASSWORD,
    _SystemTty,
    amain,
)
from app.core.config import Settings
from app.core.errors import ConflictError
from app.core.text import mask_email
from app.modules.auth.passwords import PasswordHasher, common_passwords, normalize_password
from app.modules.rbac.models import RoleAssignment
from app.modules.rbac.service import AssignmentService
from app.modules.users.models import User
from app.modules.users.repository import UserRepository
from tests.auth_factories import (
    LOGIN,
    auth_headers,
    make_login_app,
    make_login_user,
    make_member_role,
    parse_set_cookie,
)
from tests.rbac_factories import grant, make_permission

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN = "/api/v1/auth/token"
EMAIL = "new.person@example.com"
PW = "Orbit-lumen-58-quartz"
PW_FULLWIDTH = "\uff2frbit-lumen-58-quartz"  # fullwidth O: NFKC-sensitive
Scope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class FakeTty:
    def __init__(
        self,
        *,
        isatty: bool = True,
        lines: Sequence[str] = (),
        secrets: Sequence[str] = (),
    ) -> None:
        self._isatty = isatty
        self.lines = list(lines)
        self.secrets = list(secrets)
        self.prompts: list[str] = []

    def isatty(self) -> bool:
        return self._isatty

    def read_line(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.lines:
            raise AssertionError("unexpected line prompt")
        return self.lines.pop(0)

    def read_secret(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.secrets:
            raise AssertionError("unexpected secret prompt")
        return self.secrets.pop(0)


def _tty(email: str = EMAIL, password: str = PW) -> FakeTty:
    return FakeTty(lines=[email], secrets=[password, password])


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


async def _run(
    argv: Sequence[str],
    *,
    scope: Scope,
    tty: Any = None,
    settings: Settings | None = None,
) -> int:
    return await amain(
        ["create-account", *argv],
        settings=settings or Settings(),
        session_scope=scope,
        tty=tty if tty is not None else _tty(),
    )


async def _user(session: AsyncSession, email: str = EMAIL) -> User | None:
    return await session.scalar(
        select(User).where(User.email == email).execution_options(populate_existing=True)
    )


async def _count(session: AsyncSession, table: str) -> int:
    assert table in {"users", "role_assignments", "email_tokens", "email_send_log"}
    return int(await session.scalar(text(f"SELECT count(*) FROM {table}")) or 0)  # noqa: S608


def _assert_no_leak(out: str, err: str, *values: str) -> None:
    both = out + err
    assert "Traceback" not in both
    assert "postgresql" not in both
    for value in values:
        assert value not in both


async def test_create_verified_user_no_join(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(["--email", EMAIL], scope=_scope(db_session)) == 0
    out, err = capsys.readouterr()
    user = await _user(db_session)
    assert user is not None
    assert out.splitlines() == [
        "created account",
        f"user_id: {user.id}",
        f"email: {mask_email(EMAIL)}",
        "joined: -",
    ]
    assert err == ""
    assert user.email_verified_at is not None
    assert user.status == "active"
    assert await PasswordHasher(Settings()).verify(user.password_hash, PW)
    assigned = await db_session.scalar(
        select(func.count()).select_from(RoleAssignment).where(RoleAssignment.user_id == user.id)
    )
    assert assigned == 0


async def test_email_verified_at_from_db_clock(db_session: AsyncSession) -> None:
    assert await _run(["--email", EMAIL], scope=_scope(db_session)) == 0
    user = await _user(db_session)
    assert user is not None
    assert user.email_verified_at == user.created_at


async def test_create_with_join_then_login_and_token(
    client: AsyncClient, db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    app = await make_login_app(db_session)
    role = await make_member_role(db_session, app)
    assert await _run(["--email", EMAIL, "--join", "stoktok"], scope=_scope(db_session)) == 0
    out, _ = capsys.readouterr()
    assert out.splitlines()[-1] == "joined: stoktok"
    user = await _user(db_session)
    assert user is not None
    ra = await db_session.scalar(select(RoleAssignment).where(RoleAssignment.user_id == user.id))
    assert ra is not None
    assert ra.role_id == role.id
    assert ra.org_id is None
    assert ra.expires_at is None
    assert ra.granted_by is None

    resp = await client.post(LOGIN, json={"email": EMAIL, "password": PW}, headers=auth_headers())
    assert resp.status_code == 200, resp.text
    morsel = parse_set_cookie(resp)
    assert morsel is not None
    resp = await client.post(
        TOKEN,
        json={"account_id": resp.json()["account_id"], "audience": "stoktok"},
        headers=auth_headers(cookie=morsel.value),
    )
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(
        resp.json()["access_token"], options={"verify_signature": False}, audience="stoktok"
    )
    assert claims["roles"] == ["stoktok:member"]
    assert claims["email_verified"] is True


async def test_create_mixed_case_email_normalized(db_session: AsyncSession) -> None:
    tty = _tty("bob.x@example.com")
    assert await _run(["--email", "  Bob.X@EXAMPLE.com "], scope=_scope(db_session), tty=tty) == 0
    assert await _user(db_session, "bob.x@example.com") is not None


async def test_create_no_email_tokens_or_send_log(db_session: AsyncSession) -> None:
    before = (await _count(db_session, "email_tokens"), await _count(db_session, "email_send_log"))
    assert await _run(["--email", EMAIL], scope=_scope(db_session)) == 0
    after = (await _count(db_session, "email_tokens"), await _count(db_session, "email_send_log"))
    assert after == before


@pytest.mark.parametrize("kind", ["unverified", "verified", "disabled", "mixed-case"])
async def test_existing_email_exit_1_unchanged(
    kind: str, db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await make_member_role(db_session, await make_login_app(db_session))
    existing = await make_login_user(
        db_session,
        "Other-pass-77-word",
        email="Mixed.Case@example.com" if kind == "mixed-case" else EMAIL,
        verified=kind != "unverified",
        status="disabled" if kind == "disabled" else "active",
    )
    snapshot = (
        existing.password_hash,
        existing.email_verified_at,
        existing.status,
        existing.updated_at,
    )
    email = "mixed.case@example.com" if kind == "mixed-case" else EMAIL
    argv = ["--email", email, "--join", "stoktok"]
    assert await _run(argv, scope=_savepoint_scope(db_session), tty=_tty(email)) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_EMAIL_TAKEN + "\n"
    after = await _user(db_session, existing.email)
    assert after is not None
    assert snapshot == (
        after.password_hash,
        after.email_verified_at,
        after.status,
        after.updated_at,
    )
    assigned = await db_session.scalar(
        select(func.count()).select_from(RoleAssignment).where(RoleAssignment.user_id == after.id)
    )
    assert assigned == 0


def _weak_cases() -> list[tuple[str, str]]:
    common = next(p for p in sorted(common_passwords()) if len(p) >= 12)
    return [
        ("too_short", "Aq9-xz"),
        ("too_common", common),
        ("too_long", "Aq9-" * 33),
        ("too_short, too_common", "password"),
    ]


@pytest.mark.parametrize(("reasons", "password"), _weak_cases())
async def test_weak_password_refused(
    reasons: str,
    password: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_hash(self: PasswordHasher, password: str) -> str:
        raise AssertionError("hasher must not be called")

    monkeypatch.setattr(PasswordHasher, "hash", _no_hash)
    assert await _run(["--email", EMAIL], scope=_never, tty=_tty(EMAIL, password)) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == MSG_WEAK_PASSWORD.format(reasons=reasons) + "\n"
    _assert_no_leak(out, err)


async def test_password_max_length_128_accepted(db_session: AsyncSession) -> None:
    password = "Orbit-lumen-58-quartz-" + "x" * (128 - 22)
    assert len(password) == 128
    assert await _run(["--email", EMAIL], scope=_scope(db_session), tty=_tty(EMAIL, password)) == 0


async def test_password_mismatch_refused(capsys: pytest.CaptureFixture[str]) -> None:
    tty = FakeTty(lines=[EMAIL], secrets=[PW, PW + "x"])
    assert await _run(["--email", EMAIL], scope=_never, tty=tty) == 1
    out, err = capsys.readouterr()
    assert err == MSG_PASSWORD_MISMATCH + "\n"
    assert tty.prompts == ["retype email: ", "password: ", "repeat password: "]
    _assert_no_leak(out, err, PW)


@pytest.mark.parametrize("retyped", ["other.person@example.com", "not-an-email", ""])
async def test_email_retype_mismatch_refused(
    retyped: str, capsys: pytest.CaptureFixture[str]
) -> None:
    tty = FakeTty(lines=[retyped], secrets=[])
    assert await _run(["--email", EMAIL], scope=_never, tty=tty) == 1
    out, err = capsys.readouterr()
    assert err == MSG_EMAIL_MISMATCH + "\n"
    assert tty.prompts == ["retype email: "]
    assert out == ""


async def test_non_tty_refused_before_prompt_and_db(capsys: pytest.CaptureFixture[str]) -> None:
    tty = FakeTty(isatty=False)
    assert await _run(["--email", EMAIL], scope=_never, tty=tty) == 1
    out, err = capsys.readouterr()
    assert (out, err) == ("", MSG_NO_TTY + "\n")
    assert tty.prompts == []


async def test_getpass_warning_refused(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _warn(prompt: str = "") -> str:
        warnings.warn("cannot disable echo", getpass.GetPassWarning, stacklevel=1)
        return PW

    monkeypatch.setattr(getpass, "getpass", _warn)
    monkeypatch.setattr(_SystemTty, "isatty", lambda self: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(EMAIL + "\n"))
    assert await _run(["--email", EMAIL], scope=_never, tty=_SystemTty()) == 1
    out, err = capsys.readouterr()
    assert err.endswith(MSG_NO_TTY + "\n")
    _assert_no_leak(out, err, PW)


@pytest.mark.parametrize("failure", [EOFError, KeyboardInterrupt, "eof-line"])
async def test_prompt_eof_or_interrupt_refused(
    failure: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(prompt: str = "") -> str:
        raise failure

    if failure == "eof-line":
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    else:
        monkeypatch.setattr(getpass, "getpass", _raise)
        monkeypatch.setattr(sys, "stdin", io.StringIO(EMAIL + "\n"))
    monkeypatch.setattr(_SystemTty, "isatty", lambda self: True)
    assert await _run(["--email", EMAIL], scope=_never, tty=_SystemTty()) == 1
    out, err = capsys.readouterr()
    assert err.endswith(MSG_ABORTED + "\n")
    _assert_no_leak(out, err)


@pytest.mark.parametrize(
    "argv",
    [
        ["--email", EMAIL, "--password", "hunter2-secret"],
        ["--email", EMAIL, "--password=hunter2-secret"],
        ["--email", EMAIL, "hunter2-secret"],
        ["--email", EMAIL, "--join", "a", "--join", "b"],
        ["--join", "a"],
        ["--email", "not-an-email"],
    ],
)
async def test_password_option_is_usage_error(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    tty = FakeTty()
    assert await _run(argv, scope=_never, tty=tty) == 64
    out, err = capsys.readouterr()
    assert err == CREATE_USAGE + "\n"
    assert out == ""
    assert "hunter2-secret" not in err
    assert tty.prompts == []


async def test_db_echo_refused_before_prompts(capsys: pytest.CaptureFixture[str]) -> None:
    tty = _tty()
    code = await _run(["--email", EMAIL], scope=_never, tty=tty, settings=Settings(db_echo=True))
    assert code == 1
    assert capsys.readouterr().err == MSG_DB_ECHO + "\n"
    assert tty.prompts == []


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("unknown", MSG_JOIN_UNKNOWN),
        ("disabled", MSG_JOIN_UNKNOWN),
        ("admin", MSG_JOIN_ADMIN),
        ("closed", MSG_JOIN_CLOSED),
        ("approval", MSG_JOIN_APPROVAL),
        ("no-default-role", MSG_JOIN_UNAVAILABLE),
        ("admin-permission-role", MSG_JOIN_UNAVAILABLE),
    ],
)
async def test_join_refusals_create_nothing(
    case: str, message: str, db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    scope = _savepoint_scope(db_session)
    if case == "disabled":
        await make_login_app(db_session, status="disabled")
    elif case == "closed":
        await make_login_app(db_session, join_policy="closed")
    elif case == "approval":
        await make_login_app(db_session, join_policy="approval")
    elif case == "no-default-role":
        await make_login_app(db_session)
    elif case == "admin-permission-role":
        app = await make_login_app(db_session)
        role = await make_member_role(db_session, app)
        perm = await make_permission(db_session, app, "stoktok:roles:manage", is_admin=True)
        await grant(db_session, role, perm)
    elif case == "admin":
        scope = _never
    assignments_before = await _count(db_session, "role_assignments")
    key = "admin" if case == "admin" else "stoktok"
    assert await _run(["--email", EMAIL, "--join", key], scope=scope) == 1
    out, err = capsys.readouterr()
    assert (out, err) == ("", message + "\n")
    assert await _user(db_session) is None
    assert await _count(db_session, "role_assignments") == assignments_before


async def test_failure_after_insert_rolls_back_user(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_member_role(db_session, await make_login_app(db_session))

    async def _boom(self: AssignmentService, *args: Any) -> None:
        raise ConflictError("boom")

    monkeypatch.setattr(AssignmentService, "_grant_locked", _boom)
    assert (
        await _run(["--email", EMAIL, "--join", "stoktok"], scope=_savepoint_scope(db_session)) == 1
    )
    assert capsys.readouterr().err == MSG_CREATE_UNEXPECTED + "\n"
    assert await _user(db_session) is None


async def test_locks_app_before_user_insert(db_session: AsyncSession) -> None:
    await make_member_role(db_session, await make_login_app(db_session))
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        assert await _run(["--email", EMAIL, "--join", "stoktok"], scope=_scope(db_session)) == 0
    finally:
        event.remove(conn, "before_cursor_execute", _record)

    def _index(pred: Callable[[str], bool]) -> int:
        return next(i for i, s in enumerate(statements) if pred(s))

    app_lock = _index(lambda s: "FROM apps" in s and "FOR NO KEY UPDATE" in s)
    user_insert = _index(lambda s: s.startswith("INSERT INTO users"))
    assignment_insert = _index(lambda s: s.startswith("INSERT INTO role_assignments"))
    assert app_lock < user_insert < assignment_insert


async def test_hash_before_session_scope(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    original = PasswordHasher.hash

    async def _hash(self: PasswordHasher, password: str) -> str:
        result = await original(self, password)
        events.append("hash")
        return result

    @asynccontextmanager
    async def _scope_events() -> AsyncIterator[AsyncSession]:
        events.append("scope")
        yield db_session

    monkeypatch.setattr(PasswordHasher, "hash", _hash)
    assert await _run(["--email", EMAIL], scope=_scope_events) == 0
    assert events == ["hash", "scope"]


@pytest.mark.parametrize("join", [False, True])
@pytest.mark.parametrize("password", [PW, PW_FULLWIDTH])
async def test_secret_not_in_output_or_logs(
    join: bool,
    password: str,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    if join:
        await make_member_role(db_session, await make_login_app(db_session))
    for name in (None, "sqlalchemy", "asyncpg"):
        caplog.set_level(logging.DEBUG, logger=name)
    argv = ["--email", EMAIL, *(["--join", "stoktok"] if join else [])]
    assert await _run(argv, scope=_scope(db_session), tty=_tty(EMAIL, password)) == 0
    out, err = capsys.readouterr()
    user = await _user(db_session)
    assert user is not None
    secrets = {password, normalize_password(password)}
    for value in secrets | {user.password_hash}:
        assert value not in out + err
    assert EMAIL not in out + err
    for record in caplog.records:
        rendered = record.getMessage() + str(record.args)
        for value in secrets:
            assert value not in rendered
        if record.name.startswith("app"):
            assert user.password_hash not in rendered


@pytest.mark.parametrize("error", [DBAPIError, IntegrityError])
async def test_db_error_no_leak(
    error: type[DBAPIError],
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _add(self: UserRepository, user: User) -> User:
        raise error(
            "INSERT INTO users",
            {"email": user.email, "password_hash": user.password_hash},
            Exception(f"DETAIL: Key (email)=({user.email}) already exists."),
        )

    monkeypatch.setattr(UserRepository, "add", _add)
    assert await _run(["--email", EMAIL], scope=_scope(db_session)) == 1
    out, err = capsys.readouterr()
    expected = MSG_EMAIL_TAKEN if error is IntegrityError else MSG_CREATE_UNEXPECTED
    assert err == expected + "\n"
    _assert_no_leak(out, err, EMAIL, "argon2")


async def test_audit_log_line_ids_only(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    assert await _run(["--email", EMAIL], scope=_scope(db_session)) == 0
    user = await _user(db_session)
    assert user is not None
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.modules.auth.provisioning" and r.getMessage().startswith("account_")
    ]
    assert lines == [
        f"account_created actor=system:cli user_id={user.id} app_id=- role_id=- assignment_id=-"
    ]


@pytest.mark.parametrize("app_env", ["local", "test"])
async def test_allowed_in_every_app_env(app_env: str, db_session: AsyncSession) -> None:
    assert (
        await _run(["--email", EMAIL], scope=_scope(db_session), settings=Settings(app_env=app_env))
        == 0
    )


def test_subprocess_non_tty_exit_1() -> None:
    email = "secret.person@example.com"
    env = {k: v for k, v in os.environ.items() if k not in {"DB_ECHO", "APP_VERSION"}}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli", "create-account", "--email", email],
        cwd=REPO_ROOT,
        env=env | {"APP_ENV": "test"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr == MSG_NO_TTY + "\n"


@pytest.mark.skipif(sys.platform != "linux", reason="needs /proc and a pty")
def test_pty_end_to_end_no_echo_no_argv() -> None:
    import pty

    env = {k: v for k, v in os.environ.items() if k not in {"DB_ECHO", "APP_VERSION"}}
    env["APP_ENV"] = "test"
    env["DATABASE_URL"] = "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none"
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.chdir(REPO_ROOT)
        argv = [sys.executable, "-m", "app.cli", "create-account", "--email", EMAIL]
        os.execve(sys.executable, argv, env)  # noqa: S606
    transcript = ""
    deadline = time.monotonic() + 30

    def _read_until(marker: str) -> None:
        nonlocal transcript
        start = len(transcript)
        while marker not in transcript[start:]:
            assert time.monotonic() < deadline, transcript
            if fd_select([fd], [], [], 0.5)[0]:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:  # child closed the pty
                    return
                if not chunk:
                    return
                transcript += chunk.decode(errors="replace")

    try:
        _read_until("retype email: ")
        os.write(fd, (EMAIL + "\n").encode())
        _read_until("password: ")
        os.write(fd, (PW + "\n").encode())
        _read_until("repeat password: ")
        for name in ("cmdline", "environ"):
            assert PW.encode() not in Path(f"/proc/{pid}/{name}").read_bytes()
        os.write(fd, (PW + "\n").encode())
        _read_until("\0never-matches")  # drain until the child exits
    finally:
        os.kill(pid, signal.SIGKILL)  # no-op when the child already exited
        _, status = os.waitpid(pid, 0)
        os.close(fd)
    assert os.waitstatus_to_exitcode(status) == 1
    assert MSG_CREATE_UNEXPECTED in transcript
    assert PW not in transcript
    assert "Traceback" not in transcript
    assert "127.0.0.1" not in transcript
    assert "nopass" not in transcript
