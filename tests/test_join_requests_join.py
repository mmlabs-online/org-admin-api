"""Cookie-side join on an `approval` app (USR-03g): 202 pending, idempotency, cooldown, audit."""

import uuid
from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.join_requests.models import AppJoinRequest
from app.modules.rbac.models import App, RoleAssignment
from app.modules.users.models import User
from tests.auth_factories import (
    auth_headers,
    make_device,
    make_login_user,
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
