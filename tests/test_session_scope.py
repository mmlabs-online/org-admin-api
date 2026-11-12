"""S2: the request transaction commits before the response (and its `Set-Cookie`) is sent."""

import pathlib
import typing
import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi.params import Depends
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep
from app.db.session import get_session
from app.modules.sessions.models import AccountSession, DeviceSession
from tests.auth_factories import (
    LOGIN,
    LOGOUT,
    auth_headers,
    login_body,
    make_device,
    make_login_app,
    make_login_user,
    recording_app,
    shift,
)
from tests.conftest import MakeStrictApp

CASES = ["login", "logout_all", "logout_rotating"]
APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

Check = Callable[[], Awaitable[None]]


def test_session_dep_is_function_scoped() -> None:
    dep = typing.get_args(SessionDep)[1]
    assert isinstance(dep, Depends)
    assert dep.dependency is get_session
    assert dep.scope == "function"


def test_get_session_declared_once() -> None:
    hits = [
        (path.relative_to(APP_DIR.parent).as_posix(), text.count("Depends(get_session"))
        for path in APP_DIR.rglob("*.py")
        if (text := path.read_text()).count("Depends(get_session")
    ]
    assert hits == [("app/api/deps.py", 1)]


async def _arrange(
    db_session: AsyncSession, case: str
) -> tuple[str, dict[str, object], dict[str, str], Check]:
    """Committed setup for `case`: (path, body, headers, check that the DB is unchanged)."""
    await make_login_app(db_session)
    user = await make_login_user(db_session)
    user_id = user.id  # plain values: a failed request's rollback expires ORM objects
    if case == "login":
        await db_session.commit()

        async def no_device() -> None:
            count = await db_session.scalar(
                select(func.count())
                .select_from(AccountSession)
                .where(AccountSession.user_id == user_id)
            )
            assert count == 0

        return LOGIN, login_body(user), auth_headers(), no_device

    raw, device, (account,) = await make_device(db_session, user)
    if case == "logout_rotating":
        await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    await db_session.commit()
    device_id, account_id, token_hash = device.id, account.id, device.token_hash

    async def unchanged() -> None:
        row = await db_session.scalar(
            select(DeviceSession)
            .where(DeviceSession.id == device_id)
            .execution_options(populate_existing=True)
        )
        assert row is not None
        assert row.revoked_at is None
        assert row.token_hash == token_hash
        acc = await db_session.scalar(
            select(AccountSession)
            .where(AccountSession.id == account_id)
            .execution_options(populate_existing=True)
        )
        assert acc is not None and acc.revoked_at is None

    body: dict[str, object] = (
        {"all": True} if case == "logout_all" else {"account_id": str(uuid.uuid4())}
    )
    return LOGOUT, body, auth_headers(cookie=raw), unchanged


async def _post(
    app: object, path: str, body: dict[str, object], headers: dict[str, str]
) -> Response:
    transport = ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=body, headers=headers)


@pytest.mark.parametrize("case", CASES)
async def test_commit_precedes_response_start(
    make_strict_app: MakeStrictApp,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    path, body, headers, _ = await _arrange(db_session, case)
    events: list[str] = []
    real_commit = db_session.commit

    async def commit() -> None:
        await real_commit()
        events.append("commit")

    monkeypatch.setattr(db_session, "commit", commit)
    resp = await _post(recording_app(make_strict_app(), events), path, body, headers)
    assert resp.status_code in {200, 204}, resp.text
    assert "set-cookie" in resp.headers
    assert "commit" in events
    assert events[-1] == "response.start"
    assert events.index("response.start") > max(i for i, e in enumerate(events) if e == "commit")


@pytest.mark.parametrize("case", CASES)
async def test_failed_commit_sends_no_set_cookie(
    make_strict_app: MakeStrictApp,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    path, body, headers, unchanged = await _arrange(db_session, case)
    fail_on_call = 2 if case == "login" else 1  # login commits its reservation first
    calls = 0
    real_commit = db_session.commit

    async def commit() -> None:
        nonlocal calls
        calls += 1
        if calls == fail_on_call:
            raise OperationalError("commit", {}, Exception("boom"))
        await real_commit()

    monkeypatch.setattr(db_session, "commit", commit)
    resp = await _post(make_strict_app(), path, body, headers)
    assert calls == fail_on_call
    assert resp.status_code == 500
    assert "set-cookie" not in resp.headers
    monkeypatch.undo()
    await unchanged()
