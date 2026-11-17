"""Staff session management (USR-10): list, revoke one, revoke all of a user's account sessions."""

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AdminWildcardRequiredError
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.audit.context import AuditContext
from app.modules.audit.models import AuditLog
from app.modules.rbac.models import App, Role
from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.sessions.staff_service import StaffSessionService
from app.modules.users.models import User
from tests.auth_factories import (
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    shift,
)
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_role, make_user, super_admin_role

V1 = "/api/v1"
TOKEN = f"{V1}/auth/token"
ITEM_KEYS = {"id", "created_at", "last_used_at", "device_last_seen_at", "ip", "user_agent"}


def _url(user: User, session_id: uuid.UUID | None = None) -> str:
    base = f"{V1}/users/{user.id}/sessions"
    return base if session_id is None else f"{base}/{session_id}"


@pytest.fixture
async def staff(db_session: AsyncSession) -> User:
    return await make_user(db_session)


@pytest.fixture
async def target(db_session: AsyncSession) -> User:
    return await make_login_user(db_session)


async def _revoked(
    db_session: AsyncSession, row_id: uuid.UUID, model: Any = AccountSession
) -> bool:
    return (await db_session.scalar(select(model.revoked_at).where(model.id == row_id))) is not None


async def _audit(db_session: AsyncSession, user_id: uuid.UUID) -> list[AuditLog]:
    """Audit rows about `user`, ordered by action (rows of one transaction share a timestamp)."""
    result = await db_session.scalars(
        select(AuditLog)
        .where(AuditLog.target_id == user_id)
        .order_by(AuditLog.action, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(result)


async def test_list_returns_active_sessions(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    _, device1, _ = await make_device(db_session, target)
    device1.ip = "203.0.113.7"
    device1.user_agent = "Agent/1.0"
    await make_device(db_session, target)
    await db_session.flush()
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.get(_url(target))
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] == 2
    by_ip = {i["ip"]: i for i in body["items"]}
    assert by_ip["203.0.113.7"]["user_agent"] == "Agent/1.0"
    assert by_ip[None]["user_agent"] is None
    assert all(i["device_last_seen_at"] for i in body["items"])


async def test_list_item_has_exact_keys(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    await make_device(db_session, target)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.get(_url(target))
    (item,) = resp.json()["items"]
    assert set(item) == ITEM_KEYS


async def test_list_orders_and_paginates(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    accounts: list[AccountSession] = []
    for delta in ("-3 hours", "-2 hours", "-1 hours"):
        _, _, (account,) = await make_device(db_session, target)
        await shift(db_session, "account_sessions", "created_at", account.id, delta)
        accounts.append(account)
    expected = [str(a.id) for a in reversed(accounts)]
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        first = (await client.get(_url(target), params={"limit": 2})).json()
        second = (await client.get(_url(target), params={"limit": 2, "offset": 2})).json()
    assert [i["id"] for i in first["items"]] == expected[:2]
    assert [i["id"] for i in second["items"]] == expected[2:]
    assert (first["total"], second["total"]) == (3, 3)
    assert (first["limit"], second["offset"]) == (2, 2)


async def test_list_excludes_inactive_sessions(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    other = await make_login_user(db_session)
    _, _, (active,) = await make_device(db_session, target)
    _, _, (revoked,) = await make_device(db_session, target)
    await db_session.execute(
        AccountSession.__table__.update()
        .where(AccountSession.id == revoked.id)
        .values(revoked_at=func.now())
    )
    _, dev_revoked, _ = await make_device(db_session, target)
    await db_session.execute(
        DeviceSession.__table__.update()
        .where(DeviceSession.id == dev_revoked.id)
        .values(revoked_at=func.now())
    )
    _, dev_idle, _ = await make_device(db_session, target)
    await shift(db_session, "device_sessions", "last_seen_at", dev_idle.id, "-31 days")
    _, dev_old, _ = await make_device(db_session, target)
    await shift(db_session, "device_sessions", "created_at", dev_old.id, "-366 days")
    await make_device(db_session, other)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.get(_url(target))
    assert [i["id"] for i in resp.json()["items"]] == [str(active.id)]
    assert resp.json()["total"] == 1


async def test_list_unknown_user_404(make_client: MakeClient, staff: User) -> None:
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.get(f"{V1}/users/{uuid.uuid4()}/sessions")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_list_bad_uuid_422(make_client: MakeClient, staff: User) -> None:
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.get(f"{V1}/users/not-a-uuid/sessions")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_list_self_403(
    make_client: MakeClient, db_session: AsyncSession, target: User
) -> None:
    await make_device(db_session, target)
    async with make_client(admin_permissions=["admin:*"], admin_user=target) as client:
        resp = await client.get(_url(target))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "self_session_change_forbidden"
    assert await _audit(db_session, target.id) == []


async def test_routes_require_admin_wildcard(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    _, _, (account,) = await make_device(db_session, target)
    perms = ["admin:users:read", "admin:users:write"]
    async with make_client(admin_permissions=perms, admin_user=staff) as client:
        responses = [
            await client.get(_url(target)),
            await client.delete(_url(target, account.id)),
            await client.delete(_url(target)),
        ]
    for resp in responses:
        assert resp.status_code == 403
        error = resp.json()["error"]
        assert error["code"] == "permission_denied"
        assert error["details"]["required"] == "admin:*"
    assert await _audit(db_session, target.id) == []
    assert not await _revoked(db_session, account.id)


async def test_revoke_one_revokes_only_that_session(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    _, _, (first,) = await make_device(db_session, target)
    _, _, (second,) = await make_device(db_session, target)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.delete(_url(target, first.id))
    assert resp.status_code == 204
    assert resp.content == b""
    assert await _revoked(db_session, first.id)
    assert not await _revoked(db_session, second.id)


async def test_revoke_one_other_users_session_404(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    other = await make_login_user(db_session)
    _, _, (theirs,) = await make_device(db_session, other)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.delete(_url(target, theirs.id))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert not await _revoked(db_session, theirs.id)
    assert await _audit(db_session, target.id) == []


async def test_revoke_one_unknown_or_already_revoked_404(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    _, _, (account,) = await make_device(db_session, target)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        unknown = await client.delete(_url(target, uuid.uuid4()))
        assert (await client.delete(_url(target, account.id))).status_code == 204
        again = await client.delete(_url(target, account.id))
    assert unknown.status_code == 404
    assert again.status_code == 404
    assert len(await _audit(db_session, target.id)) == 1  # the one real revoke


async def test_revoke_one_unknown_user_404(make_client: MakeClient, staff: User) -> None:
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.delete(f"{V1}/users/{uuid.uuid4()}/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_revoke_all_revokes_every_device(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    devices = []
    accounts = []
    for _ in range(3):
        _, device, (account,) = await make_device(db_session, target)
        devices.append(device)
        accounts.append(account)
    await shift(db_session, "device_sessions", "last_seen_at", devices[2].id, "-31 days")
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        listed = (await client.get(_url(target))).json()
        resp = await client.delete(_url(target))
    assert listed["total"] == 2
    assert resp.status_code == 204
    for account in accounts:
        assert await _revoked(db_session, account.id)
    for device in devices:
        assert not await _revoked(db_session, device.id, DeviceSession)
    (row,) = await _audit(db_session, target.id)
    assert row.changes is not None
    assert row.changes["account_sessions_revoked"]["to"] == 3


async def test_revoke_all_unknown_user_404(make_client: MakeClient, staff: User) -> None:
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.delete(f"{V1}/users/{uuid.uuid4()}/sessions")
    assert resp.status_code == 404


async def _member(db_session: AsyncSession, role: Role) -> User:
    user = await make_login_user(db_session)
    await assign(db_session, user, role)
    return user


@pytest.fixture
async def stoktok(db_session: AsyncSession) -> App:
    return await make_login_app(db_session)


async def test_revoke_all_shared_device_keeps_other_user(
    make_client: MakeClient, db_session: AsyncSession, stoktok: App, staff: User
) -> None:
    role = await make_role(db_session, stoktok, "stoktok:viewer")
    user_a, user_b = await _member(db_session, role), await _member(db_session, role)
    raw, device, (acc_a, acc_b) = await make_device(db_session, user_a, user_b)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        assert (await client.delete(_url(user_a))).status_code == 204
        headers = auth_headers(cookie=raw)
        gone = await client.post(
            TOKEN, json={"audience": "stoktok", "account_id": str(acc_a.id)}, headers=headers
        )
        kept = await client.post(
            TOKEN, json={"audience": "stoktok", "account_id": str(acc_b.id)}, headers=headers
        )
    assert gone.status_code == 401
    assert gone.json()["error"]["code"] == "session_invalid"
    assert kept.status_code == 200, kept.text
    assert not await _revoked(db_session, device.id, DeviceSession)


async def test_token_after_revoke_one_is_session_invalid(
    make_client: MakeClient, db_session: AsyncSession, stoktok: App, staff: User
) -> None:
    role = await make_role(db_session, stoktok, "stoktok:viewer")
    user = await _member(db_session, role)
    raw, _, (account,) = await make_device(db_session, user)
    body = {"audience": "stoktok", "account_id": str(account.id)}
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        assert (
            await client.post(TOKEN, json=body, headers=auth_headers(cookie=raw))
        ).status_code == 200
        assert (await client.delete(_url(user, account.id))).status_code == 204
        resp = await client.post(TOKEN, json=body, headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"


async def test_token_after_revoke_all_is_session_invalid(
    make_client: MakeClient, db_session: AsyncSession, stoktok: App, staff: User
) -> None:
    role = await make_role(db_session, stoktok, "stoktok:viewer")
    user = await _member(db_session, role)
    sessions = [await make_device(db_session, user) for _ in range(2)]
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        assert (await client.delete(_url(user))).status_code == 204
        for raw, _, (account,) in sessions:
            resp = await client.post(
                TOKEN,
                json={"audience": "stoktok", "account_id": str(account.id)},
                headers=auth_headers(cookie=raw),
            )
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == "session_invalid"


async def test_revoke_audit_changes_exact_keys(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    _, dev1, (one,) = await make_device(db_session, target)
    dev1.ip = "203.0.113.7"
    dev1.user_agent = "Agent/1.0"
    _, _, (two,) = await make_device(db_session, target)
    await db_session.flush()
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        assert (await client.delete(_url(target, one.id))).status_code == 204
        assert (await client.delete(_url(target))).status_code == 204
    single, bulk = await _audit(db_session, target.id)
    for row, action in ((single, "user.session_revoked"), (bulk, "user.sessions_revoked")):
        assert (row.action, row.outcome, row.actor_user_id) == (action, "success", staff.id)
        assert (row.target_type, row.target_id, row.app_key) == ("user", target.id, None)
        assert row.changes is not None
        assert set(row.changes) == {"account_session_ids", "account_sessions_revoked"}
        text = str(row.changes)
        assert "203.0.113.7" not in text and "Agent" not in text
    assert single.changes == {
        "account_session_ids": {"from": None, "to": [str(one.id)]},
        "account_sessions_revoked": {"from": None, "to": 1},
    }
    assert bulk.changes == {
        "account_session_ids": {"from": None, "to": [str(two.id)]},
        "account_sessions_revoked": {"from": None, "to": 1},
    }


async def test_revoke_all_audit_caps_ids(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    for _ in range(101):
        await make_device(db_session, target)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.delete(_url(target))
    assert resp.status_code == 204
    (row,) = await _audit(db_session, target.id)
    assert row.changes is not None
    assert row.changes["account_sessions_revoked"]["to"] == 101
    ids = row.changes["account_session_ids"]["to"]
    assert len(ids) == 100
    assert ids == sorted(ids)


async def test_service_privileged_target_requires_wildcard(
    db_session: AsyncSession, staff: User
) -> None:
    """G5 defence in depth: unreachable over HTTP while the routes need `admin:*`."""
    holder = await make_login_user(db_session)
    await assign(db_session, holder, await super_admin_role(db_session))
    _, _, (account,) = await make_device(db_session, holder)
    await db_session.commit()  # a denied write rolls back first: keep the setup
    principal = AdminPrincipal(staff.id, frozenset({"admin:users:write"}), "t")
    service = StaffSessionService(db_session, idle_days=30, max_days=365)
    ctx = AuditContext.NONE
    holder_id, account_id = holder.id, account.id

    with pytest.raises(AdminWildcardRequiredError):
        await service.list(holder_id, actor=principal, limit=10, offset=0)
    assert await _audit(db_session, holder_id) == []
    with pytest.raises(AdminWildcardRequiredError):
        await service.revoke_one(holder_id, account_id, actor=principal, ctx=ctx)
    with pytest.raises(AdminWildcardRequiredError):
        await service.revoke_all(holder_id, actor=principal, ctx=ctx)

    rows = await _audit(db_session, holder_id)
    assert sorted((r.action, r.outcome) for r in rows) == [
        ("user.session_revoked", "denied"),
        ("user.sessions_revoked", "denied"),
    ]
    assert all(r.changes == {"reason": "admin_wildcard_required"} for r in rows)
    assert not await _revoked(db_session, account_id)
