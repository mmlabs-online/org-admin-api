"""GET /api/v1/stats/dashboard (ADM-015): counters, window, auth, read-only."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.repository import RbacRepository
from tests.admin_auth_factories import bearer, make_staff, mint
from tests.auth_factories import make_login_user
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_app, make_org, make_role

URL = "/api/v1/stats/dashboard"
TOP_KEYS = {
    "generated_at",
    "users",
    "organizations",
    "apps",
    "sign_ins_24h",
    "sign_ups",
    "members_per_app",
}


async def _get(make_client: MakeClient) -> dict[str, Any]:
    async with make_client(admin_permissions=["admin:stats:read"]) as c:
        resp = await c.get(URL)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _audit(db: AsyncSession, action: str, age: str, *, user: bool = False) -> None:
    actor = (await make_login_user(db)).id if user else None
    await db.execute(
        text(
            "INSERT INTO audit_log (id, actor_kind, actor_user_id, action, occurred_at) "
            "VALUES (gen_random_uuid(), :kind, :uid, :action, "
            "now() - CAST(CAST(:age AS text) AS interval))"
        ),
        {"kind": "user" if user else "anonymous", "uid": actor, "action": action, "age": age},
    )


async def test_dashboard_shape(make_client: MakeClient) -> None:
    body = await _get(make_client)
    assert set(body) == TOP_KEYS
    assert set(body["users"]["by_status"]) == {"active", "disabled"}
    assert set(body["organizations"]["by_status"]) == {"active", "suspended"}
    assert set(body["apps"]["by_status"]) == {"active", "disabled"}
    months = body["sign_ups"]["by_month"]
    assert len(months) == 12
    assert [m["month"] for m in months] == sorted(m["month"] for m in months)
    assert body["sign_ups"]["time_zone"] == "Asia/Ho_Chi_Minh"
    generated = datetime.fromisoformat(body["generated_at"])
    local = generated.astimezone(UTC) + timedelta(hours=7)
    assert months[-1]["month"] == f"{local.year:04d}-{local.month:02d}"
    assert not {"items", "total", "limit", "offset"} & set(body)


async def test_user_counts(make_client: MakeClient, db_session: AsyncSession) -> None:
    before = (await _get(make_client))["users"]
    await make_login_user(db_session, verified=True)
    await make_login_user(db_session, verified=False)
    await make_login_user(db_session, verified=False, status="disabled")
    await make_login_user(db_session, verified=True, status="disabled")
    after = (await _get(make_client))["users"]
    assert after["total"] - before["total"] == 4
    assert after["by_status"]["active"] - before["by_status"]["active"] == 2
    assert after["by_status"]["disabled"] - before["by_status"]["disabled"] == 2
    assert after["unverified"] - before["unverified"] == 2


async def test_org_counts(make_client: MakeClient, db_session: AsyncSession) -> None:
    before = (await _get(make_client))["organizations"]
    await make_org(db_session)
    await make_org(db_session)
    await make_org(db_session, status="suspended")
    after = (await _get(make_client))["organizations"]
    assert after["total"] - before["total"] == 3
    assert after["by_status"]["active"] - before["by_status"]["active"] == 2
    assert after["by_status"]["suspended"] - before["by_status"]["suspended"] == 0 + 1


async def test_app_counts(make_client: MakeClient, db_session: AsyncSession) -> None:
    before = (await _get(make_client))["apps"]
    assert before["by_status"]["active"] >= 1  # the seeded `admin` app
    await make_app(db_session, "app-one")
    await make_app(db_session, "app-two", status="disabled")
    after = (await _get(make_client))["apps"]
    assert after["total"] - before["total"] == 2
    assert after["by_status"]["active"] - before["by_status"]["active"] == 1
    assert after["by_status"]["disabled"] - before["by_status"]["disabled"] == 1


@pytest.mark.backdate_audit
async def test_sign_ins_24h_counts(make_client: MakeClient, db_session: AsyncSession) -> None:
    before = (await _get(make_client))["sign_ins_24h"]
    await _audit(db_session, "auth.login_succeeded", "1 hour", user=True)
    await _audit(db_session, "auth.login_succeeded", "2 hours", user=True)
    await _audit(db_session, "auth.login_failed", "1 hour")
    await _audit(db_session, "auth.login_failed", "3 days")
    await _audit(db_session, "auth.logout", "1 hour", user=True)
    await _audit(db_session, "organization.created", "1 hour", user=True)
    body = await _get(make_client)
    after = body["sign_ins_24h"]
    assert after["succeeded"] - before["succeeded"] == 2
    assert after["failed"] - before["failed"] == 1
    start = datetime.fromisoformat(after["window_start"])
    end = datetime.fromisoformat(after["window_end"])
    assert end == datetime.fromisoformat(body["generated_at"])
    assert end - start == timedelta(hours=24)


@pytest.mark.backdate_audit
async def test_sign_ins_24h_boundary(make_client: MakeClient, db_session: AsyncSession) -> None:
    before = (await _get(make_client))["sign_ins_24h"]["failed"]
    await _audit(db_session, "auth.login_failed", "24 hours")
    await _audit(db_session, "auth.login_failed", "23 hours 59 minutes")
    await _audit(db_session, "auth.login_failed", "24 hours 1 second")
    assert (await _get(make_client))["sign_ins_24h"]["failed"] - before == 2


async def _backdate_user(db: AsyncSession, interval: str) -> None:
    user = await make_login_user(db)
    await db.execute(
        text(
            "UPDATE users SET created_at = now() - CAST(CAST(:i AS text) AS interval) "
            "WHERE id = :id"
        ),
        {"i": interval, "id": user.id},
    )


async def test_sign_ups_by_month_api(make_client: MakeClient, db_session: AsyncSession) -> None:
    before = {m["month"]: m["count"] for m in (await _get(make_client))["sign_ups"]["by_month"]}
    await _backdate_user(db_session, "0 seconds")
    await _backdate_user(db_session, "60 days")
    await _backdate_user(db_session, "400 days")
    after = (await _get(make_client))["sign_ups"]["by_month"]
    deltas = [m["count"] - before[m["month"]] for m in after]
    assert deltas[-1] == 1  # current month
    assert deltas[-3] + deltas[-2] + deltas[-4] >= 1  # the 60-day-old user
    assert sum(deltas) == 2  # the 400-day-old user is outside every bucket
    assert all(m["count"] >= 0 for m in after)


async def test_members_per_app_api(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "zz-members")
    await make_app(db_session, "zz-empty", status="disabled")
    role = await make_role(db_session, app, "zz:member")
    org = await make_org(db_session)
    user = await make_login_user(db_session)
    await assign(db_session, user, role)
    await assign(db_session, user, role, org_id=org.id)
    body = await _get(make_client)
    rows = body["members_per_app"]
    keys = [r["app_key"] for r in rows]
    assert keys == sorted(keys)
    assert {"admin", "zz-members", "zz-empty"} <= set(keys)
    by_key = {r["app_key"]: r for r in rows}
    assert by_key["zz-members"]["member_count"] == 1
    assert by_key["zz-members"]["name"] == "Zz-Members"
    assert by_key["zz-empty"]["member_count"] == 0
    counts = await RbacRepository(db_session).member_counts()
    apps = await RbacRepository(db_session).list_app_summaries()
    assert {r["app_key"]: r["member_count"] for r in rows} == {
        key: counts.get(app_id, 0) for app_id, key, _, _ in apps
    }


async def test_401_without_token(client: AsyncClient) -> None:
    resp = await client.get(URL)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert resp.headers["www-authenticate"].startswith("Bearer")


async def test_403_without_stats_permission(make_client: MakeClient) -> None:
    async with make_client(
        admin_permissions=["admin:users:read", "admin:apps:read", "admin:audit:read"]
    ) as c:
        resp = await c.get(URL)
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "permission_denied"
    assert error["details"]["required"] == "admin:stats:read"


async def test_stats_permission_allows(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:stats:read")
    resp = await client.get(URL, headers=bearer(mint(staff)))
    assert resp.status_code == 200, resp.text


async def test_wildcard_allows(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=["admin:*"]) as c:
        assert (await c.get(URL)).status_code == 200


async def test_cache_control_no_store(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"


async def test_no_audit_row_written(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    count = text("SELECT count(*) FROM audit_log")
    before = await db_session.scalar(count)
    assert (await admin_client.get(URL)).status_code == 200
    assert await db_session.scalar(count) == before


async def _statements(make_client: MakeClient, db_session: AsyncSession) -> int:
    engine = (await db_session.connection()).sync_connection.engine
    seen: list[str] = []

    def _listener(*args: Any) -> None:
        seen.append(args[2])

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        async with make_client(admin_permissions=["admin:stats:read"]) as c:
            assert (await c.get(URL)).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _listener)
    return len(seen)


async def test_dashboard_query_count(make_client: MakeClient, db_session: AsyncSession) -> None:
    await make_app(db_session, f"qa-{uuid.uuid4().hex[:6]}")
    few = await _statements(make_client, db_session)
    for _ in range(4):
        await make_app(db_session, f"qb-{uuid.uuid4().hex[:6]}")
    many = await _statements(make_client, db_session)
    assert few == many == 7
