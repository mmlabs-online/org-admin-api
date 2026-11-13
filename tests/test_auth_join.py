"""POST /api/v1/auth/apps/{key}/join (USR-03b): join policy, idempotency, checks, audit."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.recorder import LogAuditRecorder
from app.modules.auth import router as auth_router
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.sessions.service import DeviceSessionService
from app.modules.users.models import User
from tests.auth_factories import (
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    make_member_role,
    parse_set_cookie,
    sha256,
    shift,
)
from tests.conftest import MakeClient
from tests.rbac_factories import assign, grant, make_permission, make_role

JOIN = "/api/v1/auth/apps/{key}/join"
TOKEN = "/api/v1/auth/token"
ADMIN_ORIGIN = "https://admin.mmlabs.online"


class FakeRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, uuid.UUID]]] = []

    async def record(
        self, event: str, *, actor_user_id: uuid.UUID, app_id: uuid.UUID, **ids: uuid.UUID
    ) -> None:
        self.calls.append((event, {"actor_user_id": actor_user_id, "app_id": app_id, **ids}))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> FakeRecorder:
    """Replaces what `get_audit_recorder` returns for every app built in the test."""
    fake = FakeRecorder()
    monkeypatch.setattr(auth_router, "_audit_recorder", fake)
    return fake


@pytest.fixture
async def stoktok(db_session: AsyncSession) -> App:
    return await make_login_app(db_session)


@pytest.fixture
async def member_role(db_session: AsyncSession, stoktok: App) -> Role:
    return await make_member_role(db_session, stoktok)


def _url(key: str = "stoktok") -> str:
    return JOIN.format(key=key)


def _body(account_id: uuid.UUID) -> dict[str, str]:
    return {"account_id": str(account_id)}


def _error(resp: Any) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


async def _signed_in(
    db_session: AsyncSession, **user_kwargs: Any
) -> tuple[User, str, AccountSession]:
    user = await make_login_user(db_session, **user_kwargs)
    raw, _, (account,) = await make_device(db_session, user)
    return user, raw, account


async def _assignments(db_session: AsyncSession, user: User) -> list[RoleAssignment]:
    rows = await db_session.scalars(
        select(RoleAssignment)
        .where(RoleAssignment.user_id == user.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _join(
    client: AsyncClient, account_id: uuid.UUID, raw: str, key: str = "stoktok", **headers: Any
) -> Any:
    return await client.post(
        _url(key), json=_body(account_id), headers=auth_headers(cookie=raw, **headers)
    )


async def test_join_open_assigns_default_role(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    user, raw, account = await _signed_in(db_session)
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "member"}
    (ra,) = await _assignments(db_session, user)
    assert (ra.role_id, ra.org_id, ra.expires_at, ra.granted_by) == (
        member_role.id,
        None,
        None,
        None,
    )
    assert len(recorder.calls) == 1


async def test_join_then_token_200(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    _, raw, account = await _signed_in(db_session)
    token_body = {"audience": "stoktok", "account_id": str(account.id)}
    before = await client.post(TOKEN, json=token_body, headers=auth_headers(cookie=raw))
    assert before.status_code == 403
    assert _error(before)["code"] == "not_member"
    assert _error(before)["details"] == {"join_available": True}

    assert (await _join(client, account.id, raw)).status_code == 200

    after = await client.post(TOKEN, json=token_body, headers=auth_headers(cookie=raw))
    assert after.status_code == 200, after.text
    claims = jwt.decode(after.json()["access_token"], options={"verify_signature": False})
    assert claims["roles"] == ["stoktok:member"]


async def test_join_idempotent_no_write(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    user, raw, account = await _signed_in(db_session)
    first = await _join(client, account.id, raw)
    second = await _join(client, account.id, raw)
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json() == {"status": "member"}
    assert len(await _assignments(db_session, user)) == 1
    assert len(recorder.calls) == 1


async def test_join_existing_other_role_no_write(
    client: AsyncClient,
    db_session: AsyncSession,
    stoktok: App,
    member_role: Role,
    recorder: FakeRecorder,
) -> None:
    viewer = await make_role(db_session, stoktok, "stoktok:viewer")
    user, raw, account = await _signed_in(db_session)
    await assign(db_session, user, viewer)
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 200
    assert [ra.role_id for ra in await _assignments(db_session, user)] == [viewer.id]
    assert recorder.calls == []


async def test_join_org_scope_role_is_not_membership(
    client: AsyncClient, db_session: AsyncSession, recorder: FakeRecorder
) -> None:
    app = await make_login_app(db_session, org_mode="required")
    member = await make_member_role(db_session, app)
    org_role = await make_role(db_session, app, "stoktok:org-owner")
    org = Organization(slug=f"org-{uuid.uuid4().hex[:8]}", name="Org")
    db_session.add(org)
    await db_session.flush()
    user, raw, account = await _signed_in(db_session)
    await assign(db_session, user, org_role, org_id=org.id)
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 200, resp.text
    rows = {(ra.role_id, ra.org_id) for ra in await _assignments(db_session, user)}
    assert rows == {(org_role.id, org.id), (member.id, None)}
    assert len(recorder.calls) == 1


async def test_join_revives_expired_assignment(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    user, raw, account = await _signed_in(db_session)
    expired = await assign(
        db_session, user, member_role, expires_at=datetime.now(UTC) - timedelta(days=1)
    )
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 200, resp.text
    (ra,) = await _assignments(db_session, user)
    assert (ra.id, ra.expires_at, ra.org_id) == (expired.id, None, None)
    assert len(recorder.calls) == 1
    assert recorder.calls[0][1]["assignment_id"] == expired.id


async def test_join_unverified_403(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    user, raw, account = await _signed_in(db_session, verified=False)
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "email_unverified"
    assert await _assignments(db_session, user) == []
    assert recorder.calls == []


@pytest.mark.parametrize(
    ("join_policy", "code"),
    [("closed", "join_closed"), ("approval", "join_approval_unavailable")],
)
async def test_join_policy_refused_403(
    client: AsyncClient,
    db_session: AsyncSession,
    recorder: FakeRecorder,
    join_policy: str,
    code: str,
) -> None:
    """Covers test_join_closed_403 and test_join_approval_unavailable_403."""
    app = await make_login_app(db_session, join_policy=join_policy)
    await make_member_role(db_session, app)
    user, raw, account = await _signed_in(db_session)
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 403
    assert _error(resp)["code"] == code
    assert await _assignments(db_session, user) == []
    assert recorder.calls == []


async def test_join_null_default_role_403(
    client: AsyncClient, db_session: AsyncSession, stoktok: App, recorder: FakeRecorder
) -> None:
    await make_role(db_session, stoktok, "stoktok:member")  # exists, but not the default
    user, raw, account = await _signed_in(db_session)
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "join_unavailable"
    assert await _assignments(db_session, user) == []


async def test_join_default_role_with_admin_permission_403(
    client: AsyncClient,
    db_session: AsyncSession,
    stoktok: App,
    member_role: Role,
    recorder: FakeRecorder,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging.getLogger("app.modules.membership.service"), "disabled", False)
    perm = await make_permission(db_session, stoktok, "stoktok:roles:manage", is_admin=True)
    await grant(db_session, member_role, perm)
    user, raw, account = await _signed_in(db_session)
    with caplog.at_level(logging.WARNING):
        resp = await _join(client, account.id, raw)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "join_unavailable"
    assert await _assignments(db_session, user) == []
    messages = [r.getMessage() for r in caplog.records]
    (line,) = [m for m in messages if "join_default_role_admin" in m]
    assert str(stoktok.id) in line and str(member_role.id) in line
    assert user.email not in caplog.text


async def test_join_admin_refused_even_if_open(
    client: AsyncClient, db_session: AsyncSession, recorder: FakeRecorder
) -> None:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.origins = [ADMIN_ORIGIN]
    admin.join_policy = "open"
    await db_session.flush()
    await make_member_role(db_session, admin)
    user, raw, account = await _signed_in(db_session)
    resp = await _join(client, account.id, raw, key="admin", app_key="admin", origin=ADMIN_ORIGIN)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "join_closed"
    assert await _assignments(db_session, user) == []


async def test_join_app_mismatch_403(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    other = await make_login_app(db_session, "ricotdin", origins=["https://ricotdin.mmlabs.online"])
    await make_member_role(db_session, other)
    user, raw, account = await _signed_in(db_session)
    for key in ("ricotdin", "nope"):
        resp = await _join(client, account.id, raw, key=key)
        assert resp.status_code == 403, key
        assert _error(resp)["code"] == "app_mismatch"
    assert await _assignments(db_session, user) == []


async def test_join_other_device_account_401(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    _, raw, _ = await _signed_in(db_session)
    foreign_user, _, foreign = await _signed_in(db_session)
    resp = await _join(client, foreign.id, raw)
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert parse_set_cookie(resp) is None  # cookie kept
    assert await _assignments(db_session, foreign_user) == []


async def test_join_unknown_account_401(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    _, raw, _ = await _signed_in(db_session)
    resp = await _join(client, uuid.uuid4(), raw)
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert parse_set_cookie(resp) is None


async def test_join_revoked_account_401(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    user, raw, account = await _signed_in(db_session)
    await db_session.execute(
        update(AccountSession)
        .where(AccountSession.id == account.id)
        .values(revoked_at=func.now())
        .execution_options(synchronize_session=False)
    )
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert await _assignments(db_session, user) == []


async def test_join_previous_cookie_newer_account_401(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    alice = await make_login_user(db_session)
    bob = await make_login_user(db_session)
    raw, device, (alice_account,) = await make_device(db_session, alice)
    await shift(db_session, "account_sessions", "created_at", alice_account.id, "-1 hour")
    service = DeviceSessionService(db_session, idle_days=30, max_days=365)
    assert (await service.resolve([raw], force_rotate=True)).new_cookie is not None
    bob_id = await service.add_account(device.id, bob.id)  # created_at == rotated_at

    newer = await _join(client, bob_id, raw)
    assert newer.status_code == 401
    assert _error(newer)["code"] == "session_invalid"
    assert await _assignments(db_session, bob) == []
    older = await _join(client, alice_account.id, raw)
    assert older.status_code == 200, older.text
    assert parse_set_cookie(older) is None


async def test_join_disabled_user_401(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    user, raw, account = await _signed_in(db_session, status="disabled")
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert await _assignments(db_session, user) == []


async def test_join_already_member_of_closed_app(
    client: AsyncClient, db_session: AsyncSession, recorder: FakeRecorder
) -> None:
    app = await make_login_app(db_session, join_policy="closed")
    viewer = await make_role(db_session, app, "stoktok:viewer")
    user, raw, account = await _signed_in(db_session)
    await assign(db_session, user, viewer)
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 200
    assert resp.json() == {"status": "member"}
    assert len(await _assignments(db_session, user)) == 1
    assert recorder.calls == []


async def test_join_locks_app_before_reads_and_writes(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    _, raw, account = await _signed_in(db_session)
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        resp = await _join(client, account.id, raw)
    finally:
        event.remove(conn, "before_cursor_execute", _record)
    assert resp.status_code == 200, resp.text

    lock = next(
        i for i, s in enumerate(statements) if "FROM apps" in s and "FOR NO KEY UPDATE" in s
    )
    guarded = [
        i
        for i, s in enumerate(statements)
        if "role_assignments" in s or "FROM roles" in s or "account_sessions" in s
    ]
    assert any(statements[i].startswith("INSERT INTO role_assignments") for i in guarded)
    assert lock < min(guarded)


async def test_join_audit_ids_only(
    client: AsyncClient,
    db_session: AsyncSession,
    stoktok: App,
    member_role: Role,
    recorder: FakeRecorder,
) -> None:
    user, raw, account = await _signed_in(db_session)
    assert (await _join(client, account.id, raw)).status_code == 200
    (ra,) = await _assignments(db_session, user)
    assert recorder.calls == [
        (
            "app.join",
            {
                "actor_user_id": user.id,
                "app_id": stoktok.id,
                "role_id": member_role.id,
                "assignment_id": ra.id,
            },
        )
    ]


async def test_join_default_recorder_logs_ids_only(
    client: AsyncClient,
    db_session: AsyncSession,
    stoktok: App,
    member_role: Role,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(auth_router._audit_recorder, LogAuditRecorder)
    for name in ("app.modules.audit.recorder", "app.modules.membership.service"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    user, raw, account = await _signed_in(db_session)
    with caplog.at_level(logging.INFO):
        assert (await _join(client, account.id, raw)).status_code == 200
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("audit ")]
    assert "event=app.join" in line
    assert f"actor_user_id={user.id}" in line
    assert f"app_id={stoktok.id}" in line
    assert f"role_id={member_role.id}" in line
    assert user.email not in caplog.text
    assert raw not in caplog.text


async def test_join_no_store_and_rotation(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    user = await make_login_user(db_session)
    raw, device, (account,) = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    morsel = parse_set_cookie(resp)
    assert morsel is not None and morsel.value
    assert morsel["path"] == "/"


async def _token_hash(db_session: AsyncSession, device_id: uuid.UUID) -> bytes:
    value = await db_session.scalar(
        select(DeviceSession.token_hash)
        .where(DeviceSession.id == device_id)
        .execution_options(populate_existing=True)
    )
    assert value is not None
    return value


async def test_join_error_after_rotation_rolls_back(
    make_strict_client: MakeClient, db_session: AsyncSession, recorder: FakeRecorder
) -> None:
    await make_login_app(db_session, join_policy="closed")
    user = await make_login_user(db_session)
    raw, device, (account,) = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    device_id, account_id = device.id, account.id
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await _join(client, account_id, raw)
        assert resp.status_code == 403
        assert _error(resp)["code"] == "join_closed"
        assert parse_set_cookie(resp) is None
        assert await _token_hash(db_session, device_id) == sha256(raw)


async def test_join_rate_limited_429(
    make_client: MakeClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    _, raw, account = await _signed_in(db_session)
    async with make_client(join_rate_limit=1) as client:
        first = await _join(client, account.id, raw)
        assert first.status_code == 200
        second = await _join(client, account.id, raw)
    assert second.status_code == 429
    assert _error(second)["code"] == "rate_limited"
    assert "retry-after" in second.headers


async def test_join_missing_cookie_401(
    client: AsyncClient, member_role: Role, recorder: FakeRecorder
) -> None:
    resp = await client.post(_url(), json=_body(uuid.uuid4()), headers=auth_headers())
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"


async def test_join_missing_app_header_403(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    user, raw, account = await _signed_in(db_session)
    resp = await _join(client, account.id, raw, app_key=None)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "app_header_required"
    assert await _assignments(db_session, user) == []


async def test_join_bad_origin_403(
    client: AsyncClient, db_session: AsyncSession, member_role: Role, recorder: FakeRecorder
) -> None:
    user, raw, account = await _signed_in(db_session)
    for origin in ("https://evil.example", None):
        resp = await _join(client, account.id, raw, origin=origin)
        assert resp.status_code == 403
        assert _error(resp)["code"] == "origin_not_allowed"
    assert await _assignments(db_session, user) == []


@pytest.mark.parametrize(
    "body",
    [
        {"account_id": str(uuid.uuid4()), "role_id": str(uuid.uuid4())},
        {},
        {"account_id": "not-a-uuid"},
    ],
)
async def test_join_body_validation_422(
    client: AsyncClient,
    db_session: AsyncSession,
    member_role: Role,
    recorder: FakeRecorder,
    body: dict[str, Any],
) -> None:
    _, raw, _ = await _signed_in(db_session)
    resp = await client.post(_url(), json=body, headers=auth_headers(cookie=raw))
    assert resp.status_code == 422
    assert _error(resp)["code"] == "validation_error"


async def test_join_disabled_app_403(
    client: AsyncClient, db_session: AsyncSession, recorder: FakeRecorder
) -> None:
    app = await make_login_app(db_session, status="disabled")
    await make_member_role(db_session, app)
    user, raw, account = await _signed_in(db_session)
    resp = await _join(client, account.id, raw)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "origin_not_allowed"
    assert await _assignments(db_session, user) == []
