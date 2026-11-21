"""ADM-040: staff overrides on /api/v1/apps/{key}/usage-metrics/{metric_id}/overrides."""

import itertools
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.usage.models import UsageLimitOverride, UsageMetric
from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.rbac_factories import make_app, make_user

V1 = "/api/v1"
SUPER = ["admin:*"]
_seq = itertools.count(1)


def _url(app_key: str, metric_id: object, user_id: object = "") -> str:
    return f"{V1}/apps/{app_key}/usage-metrics/{metric_id}/overrides" + (
        f"/{user_id}" if user_id else ""
    )


def _future(days: int = 7) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


async def _setup(db: AsyncSession, **app_kw: Any) -> tuple[str, uuid.UUID]:
    app = await make_app(db, f"ovr-{next(_seq)}", **app_kw)
    metric = UsageMetric(
        app_id=app.id, key="chat-turns", display_name="Chat", unit="count", default_limit=20
    )
    db.add(metric)
    await db.flush()
    return app.key, metric.id


async def _count(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(UsageLimitOverride)) or 0)


async def _audit(db: AsyncSession) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.target_type == "usage_limit_override")
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


@pytest.fixture
async def staff(db_session: AsyncSession) -> User:
    return await make_user(db_session)


@pytest.fixture
async def target(db_session: AsyncSession) -> User:
    return await make_user(db_session)


# --- auth -------------------------------------------------------------------------


async def test_routes_401_without_token(make_client: MakeClient) -> None:
    u, m = uuid.uuid4(), uuid.uuid4()
    async with make_client() as c:
        assert (await c.get(_url("shop", m))).status_code == 401
        assert (await c.get(_url("shop", m, u))).status_code == 401
        assert (
            await c.put(_url("shop", m, u), json={"limit": 1, "reason": "r"})
        ).status_code == 401
        assert (
            await c.request("DELETE", _url("shop", m, u), json={"reason": "r"})
        ).status_code == 401


async def test_reads_403_without_read(make_client: MakeClient, db_session: AsyncSession) -> None:
    key, mid = await _setup(db_session)
    async with make_client(admin_permissions=["admin:usage:write"]) as c:
        for resp in (await c.get(_url(key, mid)), await c.get(_url(key, mid, uuid.uuid4()))):
            assert resp.status_code == 403
            assert "admin:usage:read" in str(resp.json()["error"]["details"])


async def test_writes_403_with_read_only(
    make_client: MakeClient, db_session: AsyncSession, target: User
) -> None:
    key, mid = await _setup(db_session)
    async with make_client(admin_permissions=["admin:usage:read"]) as c:
        put = await c.put(_url(key, mid, target.id), json={"limit": 1, "reason": "r"})
        delete = await c.request("DELETE", _url(key, mid, target.id), json={"reason": "r"})
    for resp in (put, delete):
        assert resp.status_code == 403
        assert "admin:usage:write" in str(resp.json()["error"]["details"])


async def test_admin_wildcard_allows_all(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        assert (
            await c.put(_url(key, mid, target.id), json={"limit": 1, "reason": "r"})
        ).status_code == 201
        assert (await c.get(_url(key, mid))).status_code == 200
        assert (await c.get(_url(key, mid, target.id))).status_code == 200
        resp = await c.request("DELETE", _url(key, mid, target.id), json={"reason": "r"})
        assert resp.status_code == 204


# --- put --------------------------------------------------------------------------


async def test_put_creates_201_etag(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    exp = _future()
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        resp = await c.put(
            _url(key, mid, target.id), json={"limit": 100, "expires_at": exp, "reason": "vip"}
        )
    assert resp.status_code == 201, resp.text
    assert resp.headers["ETag"].startswith('"')
    body = resp.json()
    assert (body["limit"], body["active"], body["app_key"]) == (100, True, key)
    assert (body["user_id"], body["metric_id"]) == (str(target.id), str(mid))
    assert body["created_by"] == str(staff.id)
    assert "reason" not in body
    assert await _count(db_session) == 1


async def test_put_replaces_200_no_duplicate(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    other = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        first = (await c.put(_url(key, mid, target.id), json={"limit": 100, "reason": "a"})).json()
    async with make_client(admin_permissions=SUPER, admin_user=other) as c:
        resp = await c.put(_url(key, mid, target.id), json={"limit": 7, "reason": "b"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 7
    assert await _count(db_session) == 1
    assert (body["created_at"], body["created_by"]) == (first["created_at"], first["created_by"])
    assert body["updated_at"] > first["updated_at"]


async def test_put_zero_and_below_default_allowed(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        low = await c.put(_url(key, mid, target.id), json={"limit": 3, "reason": "r"})
        zero = await c.put(_url(key, mid, target.id), json={"limit": 0, "reason": "r"})
    assert (low.status_code, low.json()["limit"]) == (201, 3)
    assert (zero.status_code, zero.json()["limit"]) == (200, 0)


async def test_put_permanent_then_dated(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    url = _url(key, mid, target.id)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        a = await c.put(url, json={"limit": 5, "reason": "r"})
        b = await c.put(url, json={"limit": 5, "expires_at": _future(), "reason": "r"})
        d = await c.put(url, json={"limit": 5, "reason": "r"})
    assert a.json()["expires_at"] is None
    assert b.json()["expires_at"] is not None
    assert d.json()["expires_at"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"reason": ""},
        {"reason": "   "},
        {"reason": "x" * 501},
        {"reason": "a‮b"},
        {"limit": -1},
        {"limit": 2**53},
        {"limit": "5"},
        {"limit": 1.5},
        {"extra": 1},
    ],
)
async def test_put_422_cases(
    make_client: MakeClient,
    db_session: AsyncSession,
    staff: User,
    target: User,
    change: dict[str, Any],
) -> None:
    key, mid = await _setup(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        resp = await c.put(_url(key, mid, target.id), json={"limit": 1, "reason": "r", **change})
        missing = await c.put(_url(key, mid, target.id), json={"limit": 1})
    assert resp.status_code == 422
    assert missing.status_code == 422
    assert await _count(db_session) == 0


async def test_put_expires_at_past_or_naive_422(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        old = await c.put(
            _url(key, mid, target.id), json={"limit": 1, "expires_at": past, "reason": "r"}
        )
        naive = await c.put(
            _url(key, mid, target.id),
            json={"limit": 1, "expires_at": "2099-01-01T00:00:00", "reason": "r"},
        )
    assert old.status_code == 422
    assert old.json()["error"]["details"] == [{"field": "expires_at", "reason": "not_in_future"}]
    assert past not in old.text
    assert naive.status_code == 422
    assert await _count(db_session) == 0


async def test_put_archived_metric_allowed(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    await db_session.execute(
        text("UPDATE usage_metrics SET status = 'archived' WHERE id = :i"), {"i": mid}
    )
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        resp = await c.put(_url(key, mid, target.id), json={"limit": 1, "reason": "r"})
    assert resp.status_code == 201


async def test_put_disabled_app_allowed(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session, status="disabled")
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        resp = await c.put(_url(key, mid, target.id), json={"limit": 1, "reason": "r"})
    assert resp.status_code == 201


async def test_put_user_not_member_allowed(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    """`target` has no role in the app (no membership) and still gets an override."""
    key, mid = await _setup(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        resp = await c.put(_url(key, mid, target.id), json={"limit": 1, "reason": "r"})
    assert resp.status_code == 201


# --- 404 --------------------------------------------------------------------------


async def test_404_matrix(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    other_key, _ = await _setup(db_session)
    cases = {
        "unknown app": (f"nope-{next(_seq)}", mid, target.id),
        "admin app": ("admin", mid, target.id),
        "metric of other app": (other_key, mid, target.id),
        "unknown metric": (key, uuid.uuid4(), target.id),
        "unknown user": (key, mid, uuid.uuid4()),
    }
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        for name, (k, m, u) in cases.items():
            url = _url(k, m, u)
            responses = [
                await c.get(url),
                await c.put(url, json={"limit": 1, "reason": "r"}),
                await c.request("DELETE", url, json={"reason": "r"}),
            ]
            if name != "unknown user":
                responses.append(await c.get(_url(k, m)))
            for resp in responses:
                assert resp.status_code == 404, (name, resp.request.method, resp.text)
                assert resp.json()["error"]["code"] == "not_found"
    assert await _count(db_session) == 0


# --- reads ------------------------------------------------------------------------


async def test_get_single_and_list(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    key, mid = await _setup(db_session)
    users = [await make_user(db_session) for _ in range(3)]
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        for i, u in enumerate(users):
            await c.put(_url(key, mid, u.id), json={"limit": i, "reason": "r"})
        one = await c.get(_url(key, mid, users[1].id))
        page = await c.get(_url(key, mid), params={"limit": 2, "offset": 0})
        rest = await c.get(_url(key, mid), params={"limit": 2, "offset": 2})
        too_big = await c.get(_url(key, mid), params={"limit": 101})
    assert one.status_code == 200 and one.headers["ETag"]
    assert one.json()["limit"] == 1
    assert page.json()["total"] == 3
    # `created_at` is the (constant) transaction time here, so only the paging is asserted.
    listed = [i["limit"] for i in page.json()["items"] + rest.json()["items"]]
    assert (len(page.json()["items"]), len(rest.json()["items"])) == (2, 1)
    assert sorted(listed) == [0, 1, 2]
    assert too_big.status_code == 422


async def test_get_single_no_override_404(
    make_client: MakeClient, db_session: AsyncSession, target: User
) -> None:
    key, mid = await _setup(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        resp = await c.get(_url(key, mid, target.id))
    assert resp.status_code == 404


async def test_get_expired_override_is_inactive(
    make_client: MakeClient, db_session: AsyncSession, target: User
) -> None:
    key, mid = await _setup(db_session)
    await db_session.execute(
        text(
            "INSERT INTO usage_limit_overrides (id, user_id, metric_id, limit_value, expires_at) "
            "VALUES (:id, :u, :m, 9, now() - interval '1 minute')"
        ),
        {"id": uuid.uuid4(), "u": target.id, "m": mid},
    )
    async with make_client(admin_permissions=SUPER) as c:
        one = await c.get(_url(key, mid, target.id))
        listed = await c.get(_url(key, mid))
    assert one.json()["active"] is False
    assert [i["active"] for i in listed.json()["items"]] == [False]


# --- delete -----------------------------------------------------------------------


async def test_delete_204_then_404(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    url = _url(key, mid, target.id)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        await c.put(url, json={"limit": 1, "reason": "r"})
        first = await c.request("DELETE", url, json={"reason": "done"})
        second = await c.request("DELETE", url, json={"reason": "done"})
    assert first.status_code == 204 and first.content == b""
    assert second.status_code == 404
    assert await _count(db_session) == 0


async def test_delete_expired_override_allowed(
    make_client: MakeClient, db_session: AsyncSession, target: User
) -> None:
    key, mid = await _setup(db_session)
    await db_session.execute(
        text(
            "INSERT INTO usage_limit_overrides (id, user_id, metric_id, limit_value, expires_at) "
            "VALUES (:id, :u, :m, 9, now() - interval '1 day')"
        ),
        {"id": uuid.uuid4(), "u": target.id, "m": mid},
    )
    async with make_client(admin_permissions=SUPER) as c:
        resp = await c.request("DELETE", _url(key, mid, target.id), json={"reason": "tidy"})
    assert resp.status_code == 204


async def test_delete_without_reason_422(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    url = _url(key, mid, target.id)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        await c.put(url, json={"limit": 1, "reason": "r"})
        none = await c.delete(url)
        empty = await c.request("DELETE", url, json={})
        blank = await c.request("DELETE", url, json={"reason": "  "})
    assert (none.status_code, empty.status_code, blank.status_code) == (422, 422, 422)
    assert await _count(db_session) == 1


# --- audit ------------------------------------------------------------------------


async def test_put_set_audit_row_with_reason(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        await c.put(_url(key, mid, target.id), json={"limit": 100, "reason": "vip customer"})
    (row,) = await _audit(db_session)
    assert (row.action, row.app_key, row.org_id) == ("usage_limit_override.set", key, None)
    assert row.actor_user_id == staff.id
    assert row.changes is not None
    assert row.changes["limit_value"] == {"from": None, "to": 100}
    assert row.changes["user_id"] == {"from": None, "to": str(target.id)}
    assert row.changes["metric_id"] == {"from": None, "to": str(mid)}
    assert row.changes["reason"] == "vip customer"


async def test_put_replace_audit_row(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    url = _url(key, mid, target.id)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        await c.put(url, json={"limit": 100, "reason": "first"})
        await c.put(url, json={"limit": 50, "reason": "second"})
    rows = await _audit(db_session)
    by_action = {r.action: r for r in rows}  # same transaction time: order is not defined
    assert set(by_action) == {"usage_limit_override.set", "usage_limit_override.updated"}
    assert by_action["usage_limit_override.updated"].changes == {
        "limit_value": {"from": 100, "to": 50},
        "reason": "second",
    }


async def test_delete_audit_row(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    url = _url(key, mid, target.id)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        await c.put(url, json={"limit": 100, "reason": "first"})
        await c.request("DELETE", url, json={"reason": "cleanup"})
    row = next(r for r in await _audit(db_session) if r.action.endswith(".removed"))
    # `expires_at` was already null, so it is not a change.
    assert row.changes == {
        "user_id": {"from": str(target.id), "to": None},
        "metric_id": {"from": str(mid), "to": None},
        "limit_value": {"from": 100, "to": None},
        "reason": "cleanup",
    }


async def test_noop_put_writes_nothing(
    make_client: MakeClient, db_session: AsyncSession, staff: User, target: User
) -> None:
    key, mid = await _setup(db_session)
    url = _url(key, mid, target.id)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        first = await c.put(url, json={"limit": 100, "reason": "first"})
        again = await c.put(url, json={"limit": 100, "reason": "same"})
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.headers["ETag"] == first.headers["ETag"]
    assert len(await _audit(db_session)) == 1


async def test_reason_not_in_response_or_logs(
    make_client: MakeClient,
    db_session: AsyncSession,
    staff: User,
    target: User,
    caplog: pytest.LogCaptureFixture,
) -> None:
    key, mid = await _setup(db_session)
    url = _url(key, mid, target.id)
    caplog.set_level(logging.DEBUG)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        a = await c.put(url, json={"limit": 1, "reason": "sekret-reason-1"})
        b = await c.put(url, json={"limit": 2, "reason": "sekret-reason-2"})
        g = await c.get(url)
        d = await c.request("DELETE", url, json={"reason": "sekret-reason-3"})
    assert "sekret-reason" not in caplog.text
    for resp in (a, b, g, d):
        assert "sekret-reason" not in resp.text


async def test_openapi_put_declares_200_201_etag_and_delete_body(
    make_client: MakeClient,
) -> None:
    path = "/api/v1/apps/{key}/usage-metrics/{metric_id}/overrides/{user_id}"
    async with make_client(admin_permissions=SUPER) as c:
        ops = (await c.get("/openapi.json")).json()["paths"][path]
    for code in ("200", "201"):
        assert "ETag" in ops["put"]["responses"][code]["headers"]
    assert "ETag" in ops["get"]["responses"]["200"]["headers"]
    assert "requestBody" in ops["delete"]
    assert "204" in ops["delete"]["responses"]
