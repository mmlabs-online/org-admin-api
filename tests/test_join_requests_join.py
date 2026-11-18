"""Cookie-side join on an `approval` app (USR-03g): 202 pending, idempotency, cooldown, audit."""

import logging
import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.join_requests.models import AppJoinRequest
from app.modules.rbac.models import App, RoleAssignment
from app.modules.users.models import User
from tests.auth_factories import (
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    make_member_role,
    parse_set_cookie,
    shift,
)
from tests.conftest import MakeClient
from tests.join_request_factories import make_approval_app, make_join_request
from tests.rbac_factories import assign, grant, make_permission, make_role

TOKEN = "/api/v1/auth/token"


def _join_url(app: App) -> str:
    return f"/api/v1/auth/apps/{app.key}/join"


def _headers(app: App, raw: str) -> dict[str, str]:
    return auth_headers(app_key=app.key, cookie=raw)


async def _signed_in(db: AsyncSession, **kwargs: Any) -> tuple[User, str, uuid.UUID]:
    user = await make_login_user(db, **kwargs)
    raw, _, (account,) = await make_device(db, user)
    return user, raw, account.id


async def _join(client: AsyncClient, app: App, raw: str, account_id: uuid.UUID) -> Any:
    return await client.post(
        _join_url(app), json={"account_id": str(account_id)}, headers=_headers(app, raw)
    )


async def _requests(db: AsyncSession, app: App) -> list[AppJoinRequest]:
    rows = await db.scalars(
        select(AppJoinRequest)
        .where(AppJoinRequest.app_id == app.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _audit(db: AsyncSession, app: App) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.app_key == app.key)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_join_approval_202_pending(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    user = await make_login_user(db_session)
    raw, device, (account,) = await make_device(db_session, user)
    account_id = account.id
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    async with make_client() as client:
        resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 202, resp.text
    (row,) = await _requests(db_session, app)
    assert resp.json() == {"status": "pending", "request_id": str(row.id)}
    assert (row.status, row.user_id, row.decided_at) == ("pending", user.id, None)
    assert resp.headers["cache-control"] == "no-store"
    morsel = parse_set_cookie(resp)
    assert morsel is not None and morsel.value  # rotated cookie re-set
    count = await db_session.scalar(
        select(func.count()).select_from(RoleAssignment).where(RoleAssignment.user_id == user.id)
    )
    assert count == 0


async def test_join_approval_replay_same_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_approval_app(db_session)
    _, raw, account_id = await _signed_in(db_session)
    first = await _join(client, app, raw, account_id)
    second = await _join(client, app, raw, account_id)
    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json() == second.json()
    assert len(await _requests(db_session, app)) == 1


async def test_join_approval_audit_once(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    user, raw, account_id = await _signed_in(db_session)
    await _join(client, app, raw, account_id)
    await _join(client, app, raw, account_id)
    (request,) = await _requests(db_session, app)
    (row,) = await _audit(db_session, app)
    assert row.action == "app.join_requested"
    assert (row.actor_user_id, row.target_type, row.target_id) == (
        user.id,
        "join_request",
        request.id,
    )
    assert row.changes == {"status": {"from": None, "to": "pending"}}


async def test_join_approval_member_200(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    user, raw, account_id = await _signed_in(db_session)
    viewer = await make_role(db_session, app, f"{app.key}:viewer")
    await assign(db_session, user, viewer)
    resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 200
    assert resp.json() == {"status": "member"}
    assert await _requests(db_session, app) == []


async def test_join_approval_unusable_default_role_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    null_app = await make_approval_app(db_session)
    null_app.default_role_id = None
    admin_app = await make_approval_app(db_session)
    role = await make_role(db_session, admin_app, f"{admin_app.key}:privileged")
    admin_app.default_role_id = role.id
    perm = await make_permission(db_session, admin_app, f"{admin_app.key}:x:y")
    perm.is_admin = True
    await grant(db_session, role, perm)
    await db_session.flush()
    for app in (null_app, admin_app):
        _, raw, account_id = await _signed_in(db_session)
        resp = await _join(client, app, raw, account_id)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "join_unavailable"
        assert await _requests(db_session, app) == []


async def test_join_approval_unverified_403(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    _, raw, account_id = await _signed_in(db_session, verified=False)
    resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "email_unverified"
    assert await _requests(db_session, app) == []


async def test_join_approval_disabled_app_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_approval_app(db_session)
    app.status = "disabled"
    await db_session.flush()
    _, raw, account_id = await _signed_in(db_session)
    resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "origin_not_allowed"
    assert await _requests(db_session, app) == []


async def test_join_rejected_cooldown_403(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    user, raw, account_id = await _signed_in(db_session)
    await make_join_request(db_session, app, user, status="rejected")
    resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "join_rejected"
    assert len(await _requests(db_session, app)) == 1
    assert await _audit(db_session, app) == []


async def test_join_after_cooldown_new_request(
    client: AsyncClient, db_session: AsyncSession, make_client: MakeClient
) -> None:
    app = await make_approval_app(db_session)
    user, raw, account_id = await _signed_in(db_session)
    old = await make_join_request(db_session, app, user, status="rejected")
    await db_session.execute(
        update(AppJoinRequest)
        .where(AppJoinRequest.id == old.id)
        .values(decided_at=func.now() - timedelta(days=8))
    )
    resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 202, resp.text
    assert resp.json()["request_id"] != str(old.id)
    assert len(await _requests(db_session, app)) == 2

    # Cooldown off: a fresh rejection does not block either.
    other, raw2, account2 = await _signed_in(db_session)
    await make_join_request(db_session, app, other, status="rejected")
    async with make_client(join_reject_cooldown_seconds=0) as off:
        resp = await _join(off, app, raw2, account2)
    assert resp.status_code == 202, resp.text


async def test_token_join_available_approval(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    user, raw, account_id = await _signed_in(db_session)
    body = {"audience": app.key, "account_id": str(account_id)}

    async def available() -> bool:
        resp = await client.post(TOKEN, json=body, headers=_headers(app, raw))
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "not_member"
        value: bool = resp.json()["error"]["details"]["join_available"]
        return value

    assert await available() is True  # before the join
    assert (await _join(client, app, raw, account_id)).status_code == 202
    assert await available() is True  # pending: join answers 202 again
    await db_session.execute(
        update(AppJoinRequest)
        .where(AppJoinRequest.user_id == user.id)
        .values(status="rejected", decided_at=func.now())
    )
    assert await available() is False  # inside the cooldown


# --- USR-15: cancel then rejoin, user lock, pending cap ---------------------------------------


async def _capture(db: AsyncSession) -> tuple[list[str], Any]:
    statements: list[str] = []
    conn = db.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    return statements, lambda: event.remove(conn, "before_cursor_execute", _record)


async def _fill(db: AsyncSession, app: App, n: int, status: str = "pending") -> None:
    for _ in range(n):
        await make_join_request(db, app, await make_login_user(db), status)


async def test_join_after_cancel_202_new_request(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_approval_app(db_session)
    staff = await make_login_user(db_session)
    user = await make_login_user(db_session)
    old = await make_join_request(db_session, app, user)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as admin:
        for status in ("disabled", "active"):
            resp = await admin.patch(f"/api/v1/users/{user.id}", json={"status": status})
            assert resp.status_code == 200, resp.text
    await db_session.refresh(old)
    assert old.status == "cancelled"
    # Disable revoked the device sessions: sign in again, then join (no cooldown from a cancel).
    raw, _, (account,) = await make_device(db_session, user)
    async with make_client() as client:
        resp = await _join(client, app, raw, account.id)
    assert resp.status_code == 202, resp.text
    assert resp.json()["request_id"] != str(old.id)


async def test_join_approval_lock_order(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    _, raw, account_id = await _signed_in(db_session)
    statements, stop = await _capture(db_session)
    try:
        resp = await _join(client, app, raw, account_id)
    finally:
        stop()
    assert resp.status_code == 202, resp.text
    app_lock = next(
        i for i, s in enumerate(statements) if "FROM apps" in s and "FOR NO KEY UPDATE" in s
    )
    user_lock = next(i for i, s in enumerate(statements) if "FROM users" in s and "FOR SHARE" in s)
    insert = next(i for i, s in enumerate(statements) if s.startswith("INSERT INTO join_requests"))
    assert app_lock < user_lock < insert


async def test_join_cap_reached_403(
    make_client: MakeClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The migrations' `fileConfig` (session fixture) disables loggers that already exist.
    monkeypatch.setattr(logging.getLogger("app.modules.membership.service"), "disabled", False)
    app = await make_approval_app(db_session)
    await _fill(db_session, app, 2)
    user, raw, account_id = await _signed_in(db_session)
    audit_before = len(await _audit(db_session, app))
    async with make_client(join_max_pending_per_app=2) as client:
        with caplog.at_level(logging.WARNING, logger="app.modules.membership.service"):
            resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 403, resp.text
    error = resp.json()["error"]
    assert error["code"] == "join_queue_full"
    assert not error.get("details")
    assert len(await _requests(db_session, app)) == 2
    assert len(await _audit(db_session, app)) == audit_before
    # Logged with ids only.
    (record,) = [r for r in caplog.records if r.getMessage().startswith("join_queue_full")]
    assert record.getMessage() == f"join_queue_full app={app.key} user_id={user.id} cap=2"
    assert "@" not in record.getMessage()


async def test_join_cap_replay_same_request_202(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_approval_app(db_session)
    user, raw, account_id = await _signed_in(db_session)
    row = await make_join_request(db_session, app, user)
    await _fill(db_session, app, 1)  # at the cap of 2
    async with make_client(join_max_pending_per_app=2) as client:
        resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 202, resp.text
    assert resp.json()["request_id"] == str(row.id)


async def test_join_cap_after_cooldown_check(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_approval_app(db_session)
    user, raw, account_id = await _signed_in(db_session)
    await make_join_request(db_session, app, user, "rejected")
    await _fill(db_session, app, 2)
    async with make_client(join_max_pending_per_app=2) as client:
        resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "join_rejected"


async def test_join_cap_counts_pending_only(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_approval_app(db_session)
    other_app = await make_approval_app(db_session)
    await _fill(db_session, app, 1)  # one pending: below the cap of 2
    for status in ("approved", "rejected", "cancelled"):
        await _fill(db_session, app, 1, status)
    await _fill(db_session, other_app, 3)
    _, raw, account_id = await _signed_in(db_session)
    async with make_client(join_max_pending_per_app=2) as client:
        resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 202, resp.text


async def test_join_cap_frees_slot_after_reject(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_approval_app(db_session)
    await _fill(db_session, app, 2)
    _, raw, account_id = await _signed_in(db_session)
    async with make_client(join_max_pending_per_app=2) as client:
        assert (await _join(client, app, raw, account_id)).status_code == 403
        await db_session.execute(
            update(AppJoinRequest)
            .where(AppJoinRequest.id == (await _requests(db_session, app))[0].id)
            .values(status="rejected", decided_at=func.now())
        )
        resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 202, resp.text


async def test_join_open_ignores_cap(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await make_login_app(db_session, f"app-{uuid.uuid4().hex[:8]}", join_policy="open")
    await make_member_role(db_session, app)
    await _fill(db_session, app, 1)  # a (seeded) pending row on an open app
    _, raw, account_id = await _signed_in(db_session)
    async with make_client(join_max_pending_per_app=1) as client:
        resp = await _join(client, app, raw, account_id)
    assert resp.status_code == 200, resp.text
