"""USR-36: bulk-assign one role to up to 100 users (ADR-0029)."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import SelfAssignmentForbiddenError
from app.modules.audit.models import AuditLog
from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.rbac.service import AssignmentService
from app.modules.users.models import User
from tests.auth_factories import make_login_user
from tests.conftest import MakeClient
from tests.rbac_factories import (
    assign,
    grant,
    make_app,
    make_org,
    make_permission,
    make_role,
    make_user,
    super_admin_role,
)

V1 = "/api/v1"


@pytest.fixture
async def actor(db_session: AsyncSession) -> User:
    return await make_user(db_session)


@pytest.fixture
async def client(make_client: MakeClient, actor: User) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as c:
        yield c


def _url(role: Role | uuid.UUID, key: str = "stoktok") -> str:
    role_id = role.id if isinstance(role, Role) else role
    return f"{V1}/apps/{key}/roles/{role_id}/assignment-batches"


def _future(days: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def _past(days: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _body(users: list[User | uuid.UUID], **extra: Any) -> dict[str, Any]:
    return {"user_ids": [str(u.id if isinstance(u, User) else u) for u in users], **extra}


async def _world(db: AsyncSession, **app_kwargs: object) -> tuple[App, Role]:
    app = await make_app(db, "stoktok", **app_kwargs)
    return app, await make_role(db, app, "stoktok:viewer")


async def _admin_role(db: AsyncSession) -> Role:
    app = await db.scalar(select(App).where(App.key == "admin"))
    assert app is not None
    return await make_role(db, app, "admin:auditor")


def _error(resp: Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    error: dict[str, Any] = resp.json()["error"]
    assert error["code"] == code
    return error


def _unprocessable(resp: Response, field: str, reason: str) -> None:
    error = _error(resp, 422, "unprocessable")
    assert error["details"] == [{"field": field, "reason": reason}]


async def _counts(db: AsyncSession) -> tuple[int, int]:
    assignments = await db.scalar(select(func.count()).select_from(RoleAssignment))
    rows = await db.scalar(select(func.count()).select_from(AuditLog))
    return assignments or 0, rows or 0


async def _holder(db: AsyncSession, role: Role, **kwargs: Any) -> tuple[User, RoleAssignment]:
    user = await make_user(db)
    return user, await assign(db, user, role, **kwargs)


async def _granted_rows(db: AsyncSession, request_id: str) -> list[AuditLog]:
    """Scoped by request id: committed tests leave append-only rows behind."""
    rows = await db.scalars(
        select(AuditLog)
        .where(
            AuditLog.action == "role_assignment.granted",
            AuditLog.outcome == "success",
            AuditLog.request_id == request_id,
        )
        .order_by(AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


# --- results ----------------------------------------------------------------------------------


async def test_batch_all_created_200(
    client: AsyncClient, db_session: AsyncSession, actor: User
) -> None:
    _, role = await _world(db_session)
    users = [await make_user(db_session) for _ in range(3)]
    expires = _future(2)
    resp = await client.post(_url(role), json=_body(list(users), expires_at=expires.isoformat()))
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert [r["user_id"] for r in results] == [str(u.id) for u in users]
    for r, user in zip(results, users, strict=True):
        assert r["status"] == "created" and r["error"] is None
        ra = await db_session.get(RoleAssignment, uuid.UUID(r["assignment_id"]))
        assert ra is not None and ra.user_id == user.id and ra.granted_by == actor.id
        assert datetime.fromisoformat(r["expires_at"]) == expires
        assert set(r) == {"user_id", "status", "assignment_id", "expires_at", "error"}


async def test_batch_mixed_results_in_request_order(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _world(db_session)
    fresh = await make_user(db_session)
    active, active_ra = await _holder(db_session, role)
    expired, expired_ra = await _holder(db_session, role, expires_at=_past())
    unknown = uuid.uuid4()
    order = [expired, unknown, fresh, active]
    resp = await client.post(_url(role), json=_body(list(order)))
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert [r["user_id"] for r in results] == [
        str(u.id if isinstance(u, User) else u) for u in order
    ]
    assert [r["status"] for r in results] == ["created", "failed", "created", "unchanged"]
    assert results[0]["assignment_id"] == str(expired_ra.id)
    assert results[1]["error"] == {"code": "not_found", "message": "User not found"}
    assert results[1]["assignment_id"] is None and results[1]["expires_at"] is None
    assert results[3]["assignment_id"] == str(active_ra.id)


async def test_batch_id_equals_audit_request_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _world(db_session)
    users = [await make_user(db_session) for _ in range(2)]
    resp = await client.post(_url(role), json=_body(list(users)))
    assert resp.status_code == 200
    batch_id = resp.json()["batch_id"]
    assert batch_id == resp.headers["x-request-id"]
    rows = await _granted_rows(db_session, batch_id)
    assert len(rows) == 2 and {r.request_id for r in rows} == {batch_id}


async def test_batch_retry_all_unchanged(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session)
    users = [await make_user(db_session) for _ in range(2)]
    body = _body(list(users), expires_at=_future().isoformat())
    first = (await client.post(_url(role), json=body)).json()["results"]
    before = await _counts(db_session)
    second = (await client.post(_url(role), json=body)).json()["results"]
    assert [r["status"] for r in second] == ["unchanged", "unchanged"]
    for a, b in zip(first, second, strict=True):
        assert (a["assignment_id"], a["expires_at"]) == (b["assignment_id"], b["expires_at"])
    assert await _counts(db_session) == before


async def test_batch_active_holder_other_expiry_unchanged(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _world(db_session)
    forever, forever_ra = await _holder(db_session, role)
    limited_at = _future(5).replace(microsecond=0)
    limited, limited_ra = await _holder(db_session, role, expires_at=limited_at)
    resp = await client.post(_url(role), json=_body([forever], expires_at=_future().isoformat()))
    item = resp.json()["results"][0]
    assert item["status"] == "unchanged" and item["expires_at"] is None
    resp = await client.post(_url(role), json=_body([limited]))
    item = resp.json()["results"][0]
    assert item["status"] == "unchanged"
    assert datetime.fromisoformat(item["expires_at"]) == limited_at
    await db_session.refresh(forever_ra)
    await db_session.refresh(limited_ra)
    assert forever_ra.expires_at is None and limited_ra.expires_at == limited_at


@pytest.mark.parametrize("new_expiry", [None, "future"])
async def test_batch_expired_duplicate_created_with_old_id(
    client: AsyncClient, db_session: AsyncSession, actor: User, new_expiry: str | None
) -> None:
    _, role = await _world(db_session)
    user, ra = await _holder(db_session, role, expires_at=_past())
    ra_id = ra.id
    body = _body([user])
    if new_expiry:
        body["expires_at"] = _future(3).isoformat()
    resp = await client.post(_url(role), json=body)
    item = resp.json()["results"][0]
    assert item["status"] == "created" and item["assignment_id"] == str(ra_id)
    await db_session.refresh(ra)
    assert ra.granted_by == actor.id
    assert (ra.expires_at is None) == (new_expiry is None)
    rows = await _granted_rows(db_session, resp.json()["batch_id"])
    assert [r.target_id for r in rows] == [ra_id]


async def test_batch_all_failed_200(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session)
    before = await _counts(db_session)
    resp = await client.post(_url(role), json=_body([uuid.uuid4(), uuid.uuid4()]))
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [(r["status"], r["error"]["code"]) for r in results] == [("failed", "not_found")] * 2
    assert await _counts(db_session) == before


async def test_batch_disabled_unverified_users_and_disabled_app_created(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _world(db_session, status="disabled")
    disabled = await make_login_user(db_session, status="disabled")
    unverified = await make_login_user(db_session, verified=False)
    resp = await client.post(_url(role), json=_body([disabled, unverified]))
    assert [r["status"] for r in resp.json()["results"]] == ["created", "created"]


async def test_batch_self_non_privileged_role_created(
    client: AsyncClient, db_session: AsyncSession, actor: User
) -> None:
    _, role = await _world(db_session)
    resp = await client.post(_url(role), json=_body([actor]))
    assert resp.json()["results"][0]["status"] == "created"


# --- request-level refusals -------------------------------------------------------------------


async def test_batch_404_unknown_app(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session)
    before = await _counts(db_session)
    resp = await client.post(_url(role, key="nope"), json=_body([uuid.uuid4()]))
    _error(resp, 404, "not_found")
    assert await _counts(db_session) == before


async def test_batch_404_unknown_role(client: AsyncClient, db_session: AsyncSession) -> None:
    await _world(db_session)
    before = await _counts(db_session)
    _error(await client.post(_url(uuid.uuid4()), json=_body([uuid.uuid4()])), 404, "not_found")
    assert await _counts(db_session) == before


async def test_batch_404_role_of_other_app(client: AsyncClient, db_session: AsyncSession) -> None:
    await _world(db_session)
    other = await make_app(db_session, "other")
    other_role = await make_role(db_session, other, "other:viewer")
    before = await _counts(db_session)
    _error(await client.post(_url(other_role), json=_body([uuid.uuid4()])), 404, "not_found")
    assert await _counts(db_session) == before


async def test_batch_admin_app_unknown_role_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(_url(uuid.uuid4(), key="admin"), json=_body([uuid.uuid4()]))
    _error(resp, 404, "not_found")


@pytest.mark.parametrize(
    "body",
    [
        {"user_ids": []},
        {"user_ids": [str(uuid.uuid4()) for _ in range(101)]},
        {"user_ids": ["x"]},
        {"user_ids": [str(uuid.uuid4())], "extra": 1},
        {"user_ids": [str(uuid.uuid4())], "expires_at": "2030-01-01T00:00:00"},
        {},
    ],
    ids=["empty", "101", "bad-uuid", "unknown-field", "naive-expiry", "missing"],
)
async def test_batch_body_422(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    _, role = await _world(db_session)
    before = await _counts(db_session)
    _error(await client.post(_url(role), json=body), 422, "validation_error")
    assert await _counts(db_session) == before


async def test_batch_duplicate_user_ids_422(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session)
    user = await make_user(db_session)
    before = await _counts(db_session)
    _unprocessable(
        await client.post(_url(role), json=_body([user, user])), "user_ids", "duplicate_user_ids"
    )
    assert await _counts(db_session) == before


@pytest.mark.parametrize("key", ["admin:auditor", "admin:super-admin"])
async def test_batch_admin_app_role_422(
    client: AsyncClient, db_session: AsyncSession, key: str
) -> None:
    role = await super_admin_role(db_session) if key.endswith("super-admin") else None
    role = role or await _admin_role(db_session)
    user = await make_user(db_session)
    before = await _counts(db_session)
    resp = await client.post(_url(role, key="admin"), json=_body([user]))
    _unprocessable(resp, "role_id", "admin_app_role")
    assert await _counts(db_session) == before


async def test_batch_super_admin_role_422(client: AsyncClient, db_session: AsyncSession) -> None:
    app, role = await _world(db_session, org_mode="required")
    app.super_admin_role_id = role.id
    await db_session.flush()
    user = await make_user(db_session)
    before = await _counts(db_session)
    _unprocessable(await client.post(_url(role), json=_body([user])), "role_id", "super_admin_role")
    assert await _counts(db_session) == before


async def test_batch_org_id_not_allowed_422(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session)  # org_mode none
    org = await make_org(db_session)
    user = await make_user(db_session)
    before = await _counts(db_session)
    resp = await client.post(_url(role), json=_body([user], org_id=str(org.id)))
    _unprocessable(resp, "org_id", "org_id_not_allowed")
    assert await _counts(db_session) == before


async def test_batch_unknown_org_422(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session, org_mode="required")
    user = await make_user(db_session)
    before = await _counts(db_session)
    resp = await client.post(_url(role), json=_body([user], org_id=str(uuid.uuid4())))
    _unprocessable(resp, "org_id", "unknown_org")
    assert await _counts(db_session) == before


async def test_batch_org_scope_created(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session, org_mode="required")
    org = await make_org(db_session)
    user = await make_user(db_session)
    resp = await client.post(_url(role), json=_body([user], org_id=str(org.id)))
    assert resp.json()["results"][0]["status"] == "created"
    ra = await db_session.scalar(select(RoleAssignment).where(RoleAssignment.user_id == user.id))
    assert ra is not None and ra.org_id == org.id


async def test_batch_expires_in_past_422(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session)
    user = await make_user(db_session)
    before = await _counts(db_session)
    resp = await client.post(_url(role), json=_body([user], expires_at=_past().isoformat()))
    _unprocessable(resp, "expires_at", "expires_in_past")
    assert await _counts(db_session) == before


async def test_batch_permission_denied_403(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    _, role = await _world(db_session)
    before = await _counts(db_session)
    async with make_client(admin_permissions=["admin:users:read"], admin_user=actor) as c:
        _error(await c.post(_url(role), json=_body([uuid.uuid4()])), 403, "permission_denied")
    assert await _counts(db_session) == before


async def test_batch_admin_wildcard_required_403(
    make_strict_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    app, role = await _world(db_session)
    perm = await make_permission(db_session, app, "stoktok:*", is_admin=True)
    await grant(db_session, role, perm)
    u1, u2 = await make_user(db_session), await make_user(db_session)
    ids = [u1.id, uuid.uuid4(), u2.id]
    actor_id, role_url, role_key = actor.id, _url(role), role.key
    await db_session.commit()
    assignments, rows = await _counts(db_session)
    async with make_strict_client(admin_permissions=["admin:roles:assign"], admin_user=actor) as c:
        resp = await c.post(role_url, json=_body(list(ids)))
    _error(resp, 403, "admin_wildcard_required")
    count, total = await _counts(db_session)
    assert (count, total) == (assignments, rows + 2)
    denied = list(
        await db_session.scalars(
            select(AuditLog)
            .where(
                AuditLog.outcome == "denied", AuditLog.request_id == resp.headers["x-request-id"]
            )
            .order_by(AuditLog.id)
            .execution_options(populate_existing=True)
        )
    )
    assert {r.target_id for r in denied} == {ids[0], ids[2]}
    for r in denied:
        assert (r.action, r.target_type, r.app_key, r.org_id) == (
            "role_assignment.granted",
            "user",
            "stoktok",
            None,
        )
        assert r.changes == {"reason": "admin_wildcard_required", "role_key": role_key}
        assert r.actor_user_id == actor_id
    assert len({r.request_id for r in denied}) == 1


async def test_batch_body_411_413_415(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session)
    before = await _counts(db_session)
    url = _url(role)

    async def stream() -> AsyncIterator[bytes]:
        yield b"{}"

    resp = await client.post(url, content=stream(), headers={"Content-Type": "application/json"})
    _error(resp, 411, "length_required")
    big = b'{"user_ids": []' + b" " * (16 * 1024) + b"}"
    resp = await client.post(url, content=big, headers={"Content-Type": "application/json"})
    _error(resp, 413, "payload_too_large")
    resp = await client.post(url, content=b"{}", headers={"Content-Type": "text/plain"})
    _error(resp, 415, "unsupported_media_type")
    assert await _counts(db_session) == before


# --- reuse, audit, atomicity ------------------------------------------------------------------


async def test_batch_reuses_single_grant_logic(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, role = await _world(db_session)
    users = [await make_user(db_session) for _ in range(3)]
    calls: dict[str, int] = {"check": 0, "user": 0}
    check, grant_user = AssignmentService._check_grant_request, AssignmentService._grant_user_locked

    async def _check(self: AssignmentService, *a: Any, **k: Any) -> None:
        calls["check"] += 1
        await check(self, *a, **k)

    async def _user(self: AssignmentService, *a: Any, **k: Any) -> Any:
        calls["user"] += 1
        return await grant_user(self, *a, **k)

    monkeypatch.setattr(AssignmentService, "_check_grant_request", _check)
    monkeypatch.setattr(AssignmentService, "_grant_user_locked", _user)
    await client.post(_url(role), json=_body([*users, uuid.uuid4()]))
    assert calls == {"check": 1, "user": 3}


async def test_batch_audit_one_granted_row_per_created(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _world(db_session)
    fresh = await make_user(db_session)
    expired, expired_ra = await _holder(db_session, role, expires_at=_past())
    active, _ = await _holder(db_session, role)
    resp = await client.post(_url(role), json=_body([fresh, expired, active, uuid.uuid4()]))
    created = {r["assignment_id"] for r in resp.json()["results"] if r["status"] == "created"}
    rows = await _granted_rows(db_session, resp.json()["batch_id"])
    assert len(rows) == 2 and {str(r.target_id) for r in rows} == created
    assert all(r.target_type == "role_assignment" and r.changes for r in rows)
    assert str(expired_ra.id) in created


async def test_batch_mid_batch_denied_keeps_other_grants(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    actor: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, role = await _world(db_session)
    users = [await make_user(db_session) for _ in range(3)]
    ids = [u.id for u in users]
    bad = ids[1]
    url = _url(role)
    await db_session.commit()
    original = AssignmentService._forbid_self

    def _forbid(
        self: AssignmentService, app: App, role_id: uuid.UUID, uid: uuid.UUID, a: Any
    ) -> None:
        if uid == bad:
            raise SelfAssignmentForbiddenError("Cannot change your own admin roles")
        original(self, app, role_id, uid, a)

    monkeypatch.setattr(AssignmentService, "_forbid_self", _forbid)
    async with make_strict_client(admin_permissions=["admin:*"], admin_user=actor) as c:
        resp = await c.post(url, json=_body(list(ids)))
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert [r["status"] for r in results] == ["created", "failed", "created"]
    assert results[1]["error"]["code"] == "self_assignment_forbidden"
    assert len(await _granted_rows(db_session, resp.json()["batch_id"])) == 2
    (denied,) = await db_session.scalars(
        select(AuditLog)
        .where(AuditLog.outcome == "denied", AuditLog.request_id == resp.json()["batch_id"])
        .execution_options(populate_existing=True)
    )
    assert (denied.action, denied.target_type, denied.target_id) == (
        "role_assignment.granted",
        "user",
        bad,
    )
    assert denied.changes == {"reason": "self_assignment_forbidden", "role_key": "stoktok:viewer"}
    assert denied.request_id == resp.json()["batch_id"]
    held = await db_session.scalars(
        select(RoleAssignment.user_id).where(RoleAssignment.user_id.in_(ids))
    )
    assert set(held) == {ids[0], ids[2]}


async def test_batch_unexpected_error_rolls_back_all(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    actor: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, role = await _world(db_session)
    users = [await make_user(db_session) for _ in range(3)]
    url = _url(role)
    body = _body(list(users))
    await db_session.commit()
    before = await _counts(db_session)
    original = AssignmentService._record_grant
    calls = 0

    async def _boom(self: AssignmentService, *a: Any, **k: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("boom")
        await original(self, *a, **k)

    monkeypatch.setattr(AssignmentService, "_record_grant", _boom)
    async with make_strict_client(admin_permissions=["admin:*"], admin_user=actor) as c:
        c._transport.raise_app_exceptions = False  # type: ignore[attr-defined]
        resp = await c.post(url, json=body)
    assert resp.status_code == 500
    assert await _counts(db_session) == before


async def test_batch_query_count_bounded(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _world(db_session)
    users = [await make_user(db_session) for _ in range(12)]
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(" ".join(str(args[2]).split()))

    async def run(batch: list[User]) -> list[str]:
        statements.clear()
        event.listen(conn, "before_cursor_execute", _record)
        try:
            resp = await client.post(_url(role), json=_body(list(batch)))
        finally:
            event.remove(conn, "before_cursor_execute", _record)
        assert resp.status_code == 200, resp.text
        return list(statements)

    await run(users[:1])  # warm-up (also creates users[0]'s assignment)
    one = len(await run(users[1:2]))
    two = len(await run(users[2:4]))
    ten = await run(users[4:12] + users[:2])  # 8 new + 2 unchanged
    per_created = two - one
    assert 0 < per_created <= 8
    assert sum("FOR NO KEY UPDATE" in s and "FROM apps" in s for s in ten) == 1
    assert sum("FROM users" in s and "users.id IN" in s for s in ten) == 1
    assert sum("FROM role_assignments" in s and "IN (" in s for s in ten) == 1
    assert len(ten) <= one + 8 * per_created + 2  # unchanged users cost less than created ones
