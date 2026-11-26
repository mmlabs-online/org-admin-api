import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.diff import USER as USER_DIFF_FIELDS
from app.modules.audit.models import AuditLog
from app.modules.auth.email_verification import VERIFY, email_token_hash
from app.modules.auth.models import EmailToken
from app.modules.auth.password_reset import RESET
from app.modules.auth.repository import EmailTokenRepository
from app.modules.join_requests.models import AppJoinRequest
from app.modules.rbac.models import App
from app.modules.users import service as users_service
from app.modules.users.models import User
from tests.auth_factories import make_login_user
from tests.conftest import MakeClient
from tests.join_request_factories import make_approval_app, make_join_request
from tests.rbac_factories import assign, make_role

BASE = "/api/v1/users"
USER_READ_KEYS = {
    "id",
    "email",
    "status",
    "email_verified_at",
    "display_name",
    "created_at",
    "updated_at",
}
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def users_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"]) as c:
        yield c


async def _user(
    db_session: AsyncSession,
    email: str,
    *,
    status: str = "active",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> User:
    user = User(email=email, password_hash="h", status=status)
    if created_at is not None:
        user.created_at = created_at
    if updated_at is not None:
        user.updated_at = updated_at
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


def _emails(body: dict[str, Any]) -> list[str]:
    return [item["email"] for item in body["items"]]


# --- list (AC 1) -------------------------------------------------------------


async def test_list_orders_by_created_at_desc_then_id(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    oldest = await _user(db_session, "oldest@x.io", created_at=T0)
    middle = await _user(db_session, "middle@x.io", created_at=T0 + timedelta(days=1))
    newest = await _user(db_session, "newest@x.io", created_at=T0 + timedelta(days=3))
    tie_a = await _user(db_session, "tie-a@x.io", created_at=T0 + timedelta(days=2))
    tie_b = await _user(db_session, "tie-b@x.io", created_at=T0 + timedelta(days=2))
    ties = sorted([tie_a, tie_b], key=lambda u: u.id)

    resp = await users_client.get(BASE)
    assert resp.status_code == 200
    expected = [newest, *ties, middle, oldest]
    assert [item["id"] for item in resp.json()["items"]] == [str(u.id) for u in expected]


async def test_list_paginates_and_total_respects_filters(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _user(db_session, "a@x.io", created_at=T0 + timedelta(days=3))
    await _user(db_session, "b@x.io", created_at=T0 + timedelta(days=2), status="disabled")
    await _user(db_session, "c@x.io", created_at=T0 + timedelta(days=1))

    first = (await users_client.get(BASE, params={"limit": 2, "offset": 0})).json()
    assert _emails(first) == ["a@x.io", "b@x.io"]
    assert (first["total"], first["limit"], first["offset"]) == (3, 2, 0)

    second = (await users_client.get(BASE, params={"limit": 2, "offset": 2})).json()
    assert _emails(second) == ["c@x.io"]
    assert (second["total"], second["limit"], second["offset"]) == (3, 2, 2)

    filtered = (await users_client.get(BASE, params={"status": "disabled", "limit": 1})).json()
    assert _emails(filtered) == ["b@x.io"]
    assert filtered["total"] == 1


async def test_list_email_filter_is_case_insensitive_contains(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _user(db_session, "alice@example.com", created_at=T0 + timedelta(days=1))
    await _user(db_session, "bob@other.io", created_at=T0)

    resp = await users_client.get(BASE, params={"email": "EXAMPLE"})
    assert _emails(resp.json()) == ["alice@example.com"]

    resp = await users_client.get(BASE, params={"email": "o"})
    assert _emails(resp.json()) == ["alice@example.com", "bob@other.io"]
    assert resp.json()["total"] == 2


async def test_list_email_filter_escapes_like_wildcards(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _user(db_session, "a%b@x.io")
    await _user(db_session, "a_b@x.io")
    await _user(db_session, "a\\b@x.io")
    await _user(db_session, "axb@x.io")

    for term, expected in [
        ("%", ["a%b@x.io"]),
        ("_", ["a_b@x.io"]),
        ("\\", ["a\\b@x.io"]),
        ("a_b", ["a_b@x.io"]),
    ]:
        resp = await users_client.get(BASE, params={"email": term})
        assert resp.status_code == 200
        assert _emails(resp.json()) == expected, term


async def test_list_status_filter(users_client: AsyncClient, db_session: AsyncSession) -> None:
    await _user(db_session, "on@x.io")
    await _user(db_session, "off@x.io", status="disabled")

    disabled = (await users_client.get(BASE, params={"status": "disabled"})).json()
    assert _emails(disabled) == ["off@x.io"]
    active = (await users_client.get(BASE, params={"status": "active"})).json()
    assert _emails(active) == ["on@x.io"]


@pytest.mark.parametrize(
    "params",
    [
        {"status": "banned"},
        {"email": ""},
        {"email": "a" * 255},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
async def test_list_rejects_bad_query(users_client: AsyncClient, params: dict[str, Any]) -> None:
    resp = await users_client.get(BASE, params=params)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- get (AC 2) --------------------------------------------------------------


async def test_get_user(users_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _user(db_session, "get@x.io")
    resp = await users_client.get(f"{BASE}/{user.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(user.id)
    assert body["email"] == "get@x.io"
    assert body["status"] == "active"
    assert body["email_verified_at"] is None
    assert body["display_name"] is None


async def test_get_missing_user_returns_404(users_client: AsyncClient) -> None:
    missing = uuid.uuid4()
    resp = await users_client.get(f"{BASE}/{missing}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert str(missing) not in resp.text


async def test_get_bad_uuid_returns_422(users_client: AsyncClient) -> None:
    resp = await users_client.get(f"{BASE}/not-a-uuid")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- patch (AC 2) ------------------------------------------------------------


async def test_patch_disables_and_reenables(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "toggle@x.io")

    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "disabled"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"
    await db_session.refresh(user)
    assert user.status == "disabled"

    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "active"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    await db_session.refresh(user)
    assert user.status == "active"


async def _issue(db_session: AsyncSession, user: User, purpose: str) -> str:
    raw = f"{purpose}-{uuid.uuid4().hex}"
    await EmailTokenRepository(db_session).issue(
        user.id, purpose, email_token_hash(raw), timedelta(minutes=30)
    )
    return raw


async def _unused_tokens(db_session: AsyncSession, user: User) -> int:
    count = await db_session.scalar(
        select(func.count())
        .select_from(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.used_at.is_(None))
    )
    return count or 0


async def test_disable_invalidates_email_tokens(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "tokens@x.io")
    reset = await _issue(db_session, user, RESET)
    verify = await _issue(db_session, user, VERIFY)
    assert await _unused_tokens(db_session, user) == 2

    for status in ("disabled", "active"):
        resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": status})
        assert resp.status_code == 200, resp.text
    assert await _unused_tokens(db_session, user) == 0

    resp = await users_client.post(
        "/api/v1/auth/password/reset",
        json={"token": reset, "new_password": "a brand new long passphrase"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "token_invalid"
    resp = await users_client.post("/api/v1/auth/verify-email", json={"token": verify})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "token_invalid"


async def test_disable_leaves_other_users_tokens(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "target@x.io")
    other = await _user(db_session, "bystander@x.io")
    await _issue(db_session, user, RESET)
    await _issue(db_session, other, RESET)
    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "disabled"})
    assert resp.status_code == 200
    assert await _unused_tokens(db_session, user) == 0
    assert await _unused_tokens(db_session, other) == 1


async def test_patch_same_status_is_noop(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    past = datetime(2020, 1, 1, tzinfo=UTC)
    user = await _user(db_session, "noop@x.io", updated_at=past)

    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "active"})
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["updated_at"]) == past

    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "disabled"})
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["updated_at"]) != past


async def test_patch_missing_user_returns_404(users_client: AsyncClient) -> None:
    resp = await users_client.patch(f"{BASE}/{uuid.uuid4()}", json={"status": "disabled"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": None},
        {"status": "banned"},
        {"status": "disabled", "email": "x@y.z"},
        {"status": "disabled", "password_hash": "x"},
    ],
)
async def test_patch_rejects_invalid_bodies(
    users_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    user = await _user(db_session, "strict@x.io")
    resp = await users_client.patch(f"{BASE}/{user.id}", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    await db_session.refresh(user)
    assert (user.status, user.email, user.password_hash) == ("active", "strict@x.io", "h")


# --- response shape (AC 3) ---------------------------------------------------


async def test_user_read_has_exact_keys(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = User(email="keys@x.io", password_hash="$argon2id$secret-hash-value")
    db_session.add(user)
    await db_session.flush()

    responses = [
        await users_client.get(BASE),
        await users_client.get(f"{BASE}/{user.id}"),
        await users_client.patch(f"{BASE}/{user.id}", json={"status": "disabled"}),
    ]
    bodies = [responses[0].json()["items"][0], responses[1].json(), responses[2].json()]
    for resp, body in zip(responses, bodies, strict=True):
        assert resp.status_code == 200
        assert set(body) == USER_READ_KEYS
        assert "password_hash" not in resp.text
        assert "secret-hash-value" not in resp.text


# --- join requests cancelled on disable (USR-15) -----------------------------


async def _pending_rows(db_session: AsyncSession, user: User) -> list[AppJoinRequest]:
    rows = await db_session.scalars(
        select(AppJoinRequest)
        .where(AppJoinRequest.user_id == user.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _cancel_audit(db_session: AsyncSession, *request_ids: uuid.UUID) -> list[AuditLog]:
    """Cancel audit rows of these requests (committed rows of other tests may exist)."""
    rows = await db_session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "app.join_request_cancelled", AuditLog.target_id.in_(request_ids))
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_disable_cancels_pending_join_requests(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    staff = await _user(db_session, "staff@x.io")
    user = await _user(db_session, "target@x.io")
    other = await _user(db_session, "other@x.io")
    app_a, app_b, app_c, app_d = [await make_approval_app(db_session) for _ in range(4)]
    p1 = await make_join_request(db_session, app_a, user)
    p2 = await make_join_request(db_session, app_b, user)
    approved = await make_join_request(db_session, app_c, user, "approved")
    rejected = await make_join_request(db_session, app_d, user, "rejected")
    others = await make_join_request(db_session, app_a, other)
    before = {r.id: (r.status, r.decided_at, r.decided_by) for r in (approved, rejected, others)}

    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.patch(f"{BASE}/{user.id}", json={"status": "disabled"})
    assert resp.status_code == 200, resp.text

    rows = {r.id: r for r in await _pending_rows(db_session, user)}
    for row in (p1, p2):
        got = rows[row.id]
        assert (got.status, got.decided_by) == ("cancelled", staff.id)
        assert got.decided_at is not None
    for row in (approved, rejected):
        got = rows[row.id]
        assert (got.status, got.decided_at, got.decided_by) == before[row.id]
    await db_session.refresh(others)
    assert (others.status, others.decided_at, others.decided_by) == before[others.id]

    cancels = await _cancel_audit(db_session, p1.id, p2.id)
    assert sorted(c.target_id for c in cancels) == sorted([p1.id, p2.id])
    assert {c.app_key for c in cancels} == {app_a.key, app_b.key}
    for c in cancels:
        assert (c.target_type, c.actor_user_id, c.outcome) == ("join_request", staff.id, "success")
        assert c.changes == {"status": {"from": "pending", "to": "cancelled"}}
        assert "@" not in str(c.changes)
    disabled = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "user.disabled", AuditLog.target_id == user.id)
    )
    assert disabled == 1


async def test_disable_cancel_rolls_back_with_disable(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "self@x.io")
    row = await make_join_request(db_session, await make_approval_app(db_session), user)
    user_id, row_id = user.id, row.id
    await db_session.commit()
    async with make_strict_client(admin_permissions=["admin:*"], admin_user=user) as client:
        resp = await client.patch(f"{BASE}/{user_id}", json={"status": "disabled"})
    assert resp.status_code == 403
    status = await db_session.scalar(
        select(AppJoinRequest.status).where(AppJoinRequest.id == row_id)
    )
    assert status == "pending"
    assert await _cancel_audit(db_session, row_id) == []


async def test_disable_cancel_lock_order(make_client: MakeClient, db_session: AsyncSession) -> None:
    staff = await _user(db_session, "staff@x.io")
    user = await _user(db_session, "target@x.io")
    await make_join_request(db_session, await make_approval_app(db_session), user)
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
            resp = await client.patch(f"{BASE}/{user.id}", json={"status": "disabled"})
    finally:
        event.remove(conn, "before_cursor_execute", _record)
    assert resp.status_code == 200, resp.text
    app_locks = [
        i for i, s in enumerate(statements) if "FROM apps" in s and "FOR NO KEY UPDATE" in s
    ]
    user_lock = next(
        i for i, s in enumerate(statements) if "FROM users" in s and "FOR NO KEY UPDATE" in s
    )
    cancel = next(i for i, s in enumerate(statements) if s.startswith("UPDATE join_requests"))
    assert len(app_locks) == 1
    assert app_locks[0] < user_lock < cancel


async def test_disable_noop_cancels_nothing(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    staff = await _user(db_session, "staff@x.io")
    disabled = await _user(db_session, "off@x.io", status="disabled")
    active = await _user(db_session, "on@x.io")
    app = await make_approval_app(db_session)
    seeded = await make_join_request(db_session, app, disabled)
    live = await make_join_request(db_session, await make_approval_app(db_session), active)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as client:
        resp = await client.patch(f"{BASE}/{disabled.id}", json={"status": "disabled"})
        assert resp.status_code == 200, resp.text
        resp = await client.patch(f"{BASE}/{active.id}", json={"status": "active"})
        assert resp.status_code == 200, resp.text
    for row in (seeded, live):
        await db_session.refresh(row)
        assert row.status == "pending"
    assert await _cancel_audit(db_session, seeded.id, live.id) == []


# --- display name (USR-33) ------------------------------------------------------


async def _rows(db: AsyncSession, action: str, user_id: uuid.UUID) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.action == action, AuditLog.target_id == user_id)
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_patch_sets_display_name(users_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _user(db_session, "named@x.io")
    resp = await users_client.patch(f"{BASE}/{user.id}", json={"display_name": "  Nguyễn  Ánh "})
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == USER_READ_KEYS
    assert resp.json()["display_name"] == "Nguyễn Ánh"
    assert resp.json()["status"] == "active"


async def test_patch_null_clears_display_name(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "clear@x.io")
    user.display_name = "Old"
    await db_session.flush()
    resp = await users_client.patch(f"{BASE}/{user.id}", json={"display_name": None})
    assert resp.status_code == 200
    assert resp.json()["display_name"] is None
    await db_session.refresh(user)
    assert user.display_name is None


async def test_patch_status_and_name_together(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "both@x.io")
    resp = await users_client.patch(
        f"{BASE}/{user.id}", json={"status": "disabled", "display_name": "Both"}
    )
    assert resp.status_code == 200
    assert (resp.json()["status"], resp.json()["display_name"]) == ("disabled", "Both")
    assert len(await _rows(db_session, "user.disabled", user.id)) == 1
    assert len(await _rows(db_session, "user.display_name_changed", user.id)) == 1


async def test_patch_name_same_value_noop(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    past = datetime(2020, 1, 1, tzinfo=UTC)
    user = await _user(db_session, "same@x.io", updated_at=past)
    user.display_name = "Same"
    await db_session.flush()
    await db_session.refresh(user)
    stamp = user.updated_at
    resp = await users_client.patch(f"{BASE}/{user.id}", json={"display_name": "Same"})
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["updated_at"]) == stamp
    assert await _rows(db_session, "user.display_name_changed", user.id) == []


@pytest.mark.parametrize(
    "name", ["", "a" * 101, "zzbad\u202e", "zzbad\u3164", "zzbad\u0300\u0301\u0302", 5]
)
async def test_patch_invalid_display_name_422_no_echo(
    users_client: AsyncClient, db_session: AsyncSession, name: Any
) -> None:
    user = await _user(db_session, "bad@x.io")
    resp = await users_client.patch(f"{BASE}/{user.id}", json={"display_name": name})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert "zzbad" not in resp.text
    await db_session.refresh(user)
    assert user.display_name is None


async def test_patch_name_requires_users_write(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "ro@x.io")
    async with make_client(admin_permissions=["admin:users:read"]) as client:
        resp = await client.patch(f"{BASE}/{user.id}", json={"display_name": "Nope"})
    assert resp.status_code == 403
    await db_session.refresh(user)
    assert user.display_name is None


async def test_patch_name_unknown_user_404(users_client: AsyncClient) -> None:
    resp = await users_client.patch(f"{BASE}/{uuid.uuid4()}", json={"display_name": "Ghost"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_patch_name_privileged_target_needs_wildcard(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_login_user(db_session)
    target = await make_login_user(db_session)
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    await assign(db_session, target, await make_role(db_session, admin, "admin:auditor"))
    target_id = target.id
    await db_session.commit()  # a refused request rolls back to the last savepoint
    await db_session.refresh(actor)
    async with make_client(admin_permissions=["admin:users:write"], admin_user=actor) as client:
        denied = await client.patch(f"{BASE}/{target_id}", json={"display_name": "Nope"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "admin_wildcard_required"
    (row,) = await _rows(db_session, "user.display_name_changed", target_id)
    assert (row.outcome, row.target_id) == ("denied", target_id)
    name = await db_session.scalar(select(User.display_name).where(User.id == target_id))
    assert name is None
    await db_session.refresh(actor)
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as client:
        ok = await client.patch(f"{BASE}/{target_id}", json={"display_name": "Yes"})
    assert ok.status_code == 200
    assert ok.json()["display_name"] == "Yes"


async def test_patch_name_audit_row_has_no_value(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "audit@x.io")
    await users_client.patch(f"{BASE}/{user.id}", json={"display_name": "zzauditmarker"})
    (row,) = await _rows(db_session, "user.display_name_changed", user.id)
    assert (row.outcome, row.target_type, row.target_id) == ("success", "user", user.id)
    assert row.changes == {"display_name": {"set": True}}
    assert "zzauditmarker" not in str(row.changes)


def test_user_diff_tuple_excludes_display_name() -> None:
    assert "display_name" not in USER_DIFF_FIELDS


async def test_patch_name_logs_no_value(
    users_client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Alembic's `fileConfig` (test DB setup) disables existing loggers.
    monkeypatch.setattr(users_service.logger, "disabled", False)
    user = await _user(db_session, "logs@x.io")
    with caplog.at_level(logging.DEBUG, logger="app"):
        resp = await users_client.patch(f"{BASE}/{user.id}", json={"display_name": "zznamemarker"})
    assert resp.status_code == 200
    records = [r for r in caplog.records if "display_name_changed" in r.getMessage()]
    assert len(records) == 1  # non-vacuous: an app-module line was captured
    assert records[0].name == "app.modules.users.service"
    assert str(user.id) in records[0].getMessage()
    assert "zznamemarker" not in caplog.text
