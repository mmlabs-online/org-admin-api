"""Staff join-request routes (USR-03g): list, approve, reject."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.join_requests.models import AppJoinRequest
from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.users.models import User
from tests.auth_factories import make_login_user
from tests.conftest import MakeClient
from tests.join_request_factories import make_approval_app, make_join_request
from tests.rbac_factories import assign, grant, make_permission, make_role, make_user

V1 = "/api/v1"
STAFF = ["admin:users:read", "admin:roles:assign"]


@pytest.fixture
async def staff(db_session: AsyncSession) -> User:
    return await make_user(db_session)


def _list(app: App) -> str:
    return f"{V1}/apps/{app.key}/join-requests"


def _act(app: App, request_id: object, verb: str) -> str:
    return f"{V1}/apps/{app.key}/join-requests/{request_id}/{verb}"


async def _pending(db: AsyncSession, **user_kwargs: Any) -> tuple[App, User, AppJoinRequest]:
    app = await make_approval_app(db)
    user = await make_login_user(db, **user_kwargs)
    return app, user, await make_join_request(db, app, user)


async def _assignments(db: AsyncSession, user: User) -> list[RoleAssignment]:
    rows = await db.scalars(
        select(RoleAssignment)
        .where(RoleAssignment.user_id == user.id)
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


async def _fresh(db: AsyncSession, row: AppJoinRequest) -> AppJoinRequest:
    await db.refresh(row)
    return row


async def test_list_pending_default(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    first = await make_join_request(db_session, app, await make_login_user(db_session))
    unverified = await make_login_user(db_session, verified=False)
    second = await make_join_request(db_session, app, unverified)
    await make_join_request(db_session, app, await make_login_user(db_session), "rejected")
    async with make_client(admin_permissions=STAFF) as client:
        resp = await client.get(_list(app))
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert (body["total"], body["limit"], body["offset"]) == (2, 20, 0)
    by_id = {i["id"]: i for i in body["items"]}  # same now(): ordered by id
    assert [i["id"] for i in body["items"]] == sorted([str(first.id), str(second.id)])
    item = by_id[str(second.id)]
    assert item["email"] == unverified.email
    assert (item["email_verified"], item["user_status"], item["status"]) == (
        False,
        "active",
        "pending",
    )
    assert (item["app_key"], item["decided_at"], item["decided_by"]) == (app.key, None, None)


async def test_list_status_filter(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await make_approval_app(db_session)
    await make_join_request(db_session, app, await make_login_user(db_session))
    rejected = await make_join_request(
        db_session, app, await make_login_user(db_session), "rejected"
    )
    async with make_client(admin_permissions=STAFF) as client:
        resp = await client.get(_list(app), params={"status": "rejected"})
        assert [i["id"] for i in resp.json()["items"]] == [str(rejected.id)]
        assert (await client.get(_list(app), params={"status": "bogus"})).status_code == 422
        assert (await client.get(_list(app), params={"limit": 101})).status_code == 422


@pytest.mark.parametrize("key", ["nope-app", "admin"])
async def test_list_unknown_or_admin_app_404(make_client: MakeClient, key: str) -> None:
    async with make_client(admin_permissions=STAFF) as client:
        resp = await client.get(f"{V1}/apps/{key}/join-requests")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_approve_grants_default_role(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app, user, row = await _pending(db_session)
    async with make_client(admin_permissions=STAFF, admin_user=staff) as staff_client:
        resp = await staff_client.post(_act(app, row.id, "approve"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["status"], body["decided_by"]) == ("approved", str(staff.id))
    assert body["decided_at"] is not None
    (ra,) = await _assignments(db_session, user)
    assert (ra.role_id, ra.org_id, ra.granted_by) == (app.default_role_id, None, staff.id)
    (audit,) = await _audit(db_session, app)
    assert audit.action == "app.join_request_approved"
    assert (audit.actor_user_id, audit.target_type, audit.target_id) == (
        staff.id,
        "join_request",
        row.id,
    )
    assert audit.changes is not None and audit.changes["status"] == {
        "from": "pending",
        "to": "approved",
    }
    assert audit.changes["role_key"] == {"from": None, "to": f"{app.key}:member"}


async def test_approve_already_member_no_grant(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app, user, row = await _pending(db_session)
    viewer = await make_role(db_session, app, f"{app.key}:viewer")
    await assign(db_session, user, viewer)
    async with make_client(admin_permissions=STAFF, admin_user=staff) as client:
        resp = await client.post(_act(app, row.id, "approve"))
    assert resp.status_code == 200, resp.text
    assert [ra.role_id for ra in await _assignments(db_session, user)] == [viewer.id]
    (audit,) = await _audit(db_session, app)
    assert audit.changes is not None and "role_key" not in audit.changes


async def test_approve_uses_current_default_role(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app, user, row = await _pending(db_session)
    newer = await make_role(db_session, app, f"{app.key}:newer")
    app.default_role_id = newer.id
    await db_session.flush()
    async with make_client(admin_permissions=STAFF, admin_user=staff) as client:
        resp = await client.post(_act(app, row.id, "approve"))
    assert resp.status_code == 200, resp.text
    (ra,) = await _assignments(db_session, user)
    assert ra.role_id == newer.id
    (audit,) = await _audit(db_session, app)
    assert audit.changes is not None
    assert audit.changes["role_key"] == {"from": None, "to": newer.key}


async def _refuse_app_disabled(db: AsyncSession, app: App, user: User) -> None:
    app.status = "disabled"


async def _refuse_open(db: AsyncSession, app: App, user: User) -> None:
    app.join_policy = "open"


async def _refuse_closed(db: AsyncSession, app: App, user: User) -> None:
    app.join_policy = "closed"


async def _refuse_user_disabled(db: AsyncSession, app: App, user: User) -> None:
    user.status = "disabled"


async def _refuse_unverified(db: AsyncSession, app: App, user: User) -> None:
    user.email_verified_at = None


async def _refuse_null_role(db: AsyncSession, app: App, user: User) -> None:
    app.default_role_id = None


async def _refuse_admin_role(db: AsyncSession, app: App, user: User) -> None:
    perm = await make_permission(db, app, f"{app.key}:x:y")
    perm.is_admin = True
    assert app.default_role_id is not None
    role = await db.get(Role, app.default_role_id)
    assert role is not None
    await grant(db, role, perm)


@pytest.mark.parametrize(
    ("reason", "mutate"),
    [
        ("app_disabled", _refuse_app_disabled),
        ("join_policy_changed", _refuse_open),
        ("join_policy_changed", _refuse_closed),
        ("user_disabled", _refuse_user_disabled),
        ("email_unverified", _refuse_unverified),
        ("default_role_unusable", _refuse_null_role),
        ("default_role_unusable", _refuse_admin_role),
    ],
)
async def test_approve_refusals_409(
    make_client: MakeClient, db_session: AsyncSession, reason: str, mutate: Any
) -> None:
    app, user, row = await _pending(db_session)
    await mutate(db_session, app, user)
    await db_session.flush()
    async with make_client(admin_permissions=STAFF) as client:
        resp = await client.post(_act(app, row.id, "approve"))
    assert resp.status_code == 409, resp.text
    error = resp.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"][0]["reason"] == reason
    assert (await _fresh(db_session, row)).status == "pending"
    assert await _assignments(db_session, user) == []
    assert await _audit(db_session, app) == []


@pytest.mark.parametrize("verb", ["approve", "reject"])
async def test_not_pending_409(
    make_client: MakeClient, db_session: AsyncSession, verb: str
) -> None:
    app = await make_approval_app(db_session)
    row = await make_join_request(db_session, app, await make_login_user(db_session), "approved")
    async with make_client(admin_permissions=STAFF) as client:
        resp = await client.post(_act(app, row.id, verb))
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "not_pending"
    assert await _audit(db_session, app) == []


async def test_second_approve_409(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app, _, row = await _pending(db_session)
    async with make_client(admin_permissions=STAFF, admin_user=staff) as client:
        assert (await client.post(_act(app, row.id, "approve"))).status_code == 200
        again = await client.post(_act(app, row.id, "approve"))
    assert again.status_code == 409
    assert again.json()["error"]["details"][0]["reason"] == "not_pending"
    assert len(await _audit(db_session, app)) == 1


@pytest.mark.parametrize("verb", ["approve", "reject"])
async def test_unknown_and_cross_app_404(
    make_client: MakeClient, db_session: AsyncSession, verb: str
) -> None:
    app, _, _ = await _pending(db_session)
    other_app, _, other_row = await _pending(db_session)
    async with make_client(admin_permissions=STAFF) as client:
        unknown = await client.post(_act(app, uuid.uuid4(), verb))
        cross = await client.post(_act(app, other_row.id, verb))
    assert unknown.status_code == cross.status_code == 404
    assert unknown.json()["error"]["code"] == cross.json()["error"]["code"] == "not_found"
    assert (await _fresh(db_session, other_row)).status == "pending"
    assert other_app.key != app.key


async def test_reject_records_decision(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app, user, row = await _pending(db_session)
    async with make_client(admin_permissions=STAFF, admin_user=staff) as client:
        resp = await client.post(_act(app, row.id, "reject"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["status"], body["decided_by"]) == ("rejected", str(staff.id))
    assert body["decided_at"] is not None
    assert await _assignments(db_session, user) == []
    (audit,) = await _audit(db_session, app)
    assert audit.action == "app.join_request_rejected"


async def test_reject_allowed_when_app_disabled_or_policy_changed(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    disabled, _, row1 = await _pending(db_session)
    disabled.status = "disabled"
    changed, _, row2 = await _pending(db_session)
    changed.join_policy = "open"
    await db_session.flush()
    async with make_client(admin_permissions=STAFF, admin_user=staff) as client:
        for app, row in ((disabled, row1), (changed, row2)):
            resp = await client.post(_act(app, row.id, "reject"))
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "rejected"


async def test_approve_lock_order(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app, _, row = await _pending(db_session)
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        async with make_client(admin_permissions=STAFF, admin_user=staff) as client:
            resp = await client.post(_act(app, row.id, "approve"))
    finally:
        event.remove(conn, "before_cursor_execute", _record)
    assert resp.status_code == 200, resp.text

    def first(pred: Any) -> int:
        return next(i for i, s in enumerate(statements) if pred(s))

    app_lock = first(lambda s: "FROM apps" in s and "FOR NO KEY UPDATE" in s)
    user_lock = first(lambda s: "FROM users" in s and "FOR SHARE" in s)
    request_lock = first(lambda s: "FROM join_requests" in s and "FOR UPDATE" in s)
    insert = first(lambda s: s.startswith("INSERT INTO role_assignments"))
    assert app_lock < user_lock < request_lock < insert


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/join-requests"),
        ("POST", f"/join-requests/{uuid.uuid4()}/approve"),
        ("POST", f"/join-requests/{uuid.uuid4()}/reject"),
    ],
)
async def test_staff_routes_401_403(
    client: AsyncClient, make_client: MakeClient, method: str, path: str
) -> None:
    url = f"{V1}/apps/stoktok{path}"
    assert (await client.request(method, url)).status_code == 401
    async with make_client(admin_permissions=["admin:apps:read"]) as limited:
        resp = await limited.request(method, url)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"
