"""ADM-043: staff reads GET /api/v1/apps/{key}/user-usage/{user_id}[/periods|/events]."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableError
from app.modules.usage.models import UsageCounter, UsageEvent, UsagePeriod
from app.modules.usage.periods import period_bounds
from app.modules.usage.staff_read_service import check_range
from tests.conftest import MakeClient
from tests.rbac_factories import make_user
from tests.usage_factories import (
    V1,
    World,
    auth,
    consume,
    make_member,
    make_metric,
    override,
    refund,
    settle,
    usage_key,
    world,
)

READ = ["admin:usage:read"]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _url(w: World | str, user_id: object, route: str = "") -> str:
    key = w if isinstance(w, str) else w.app.key
    return f"{V1}/apps/{key}/user-usage/{user_id}{route}"


def _routes(w: World | str, user_id: object) -> list[str]:
    return [_url(w, user_id, r) for r in ("", "/periods", "/events")]


async def _counter(
    db: AsyncSession, w: World, start: datetime, used: int, *, metric_id: uuid.UUID | None = None
) -> None:
    db.add(
        UsageCounter(
            app_id=w.app.id,
            user_id=w.user.id,
            metric_id=metric_id or w.metric.id,
            period="day",
            period_start=start,
            used=used,
        )
    )
    await db.flush()


def test_check_range_unit() -> None:
    check_range(None, None)
    check_range(T0, None)
    check_range(None, T0)
    check_range(T0, T0 + timedelta(days=1))
    with pytest.raises(UnprocessableError):
        check_range(T0, T0)


async def test_current_default_without_override(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=5)
    async with make_client(admin_permissions=READ) as c:
        await consume(c, w, "k1", 2)
        resp = await c.get(_url(w, w.user.id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["member"] is True and body["user_id"] == str(w.user.id)
    (m,) = body["metrics"]
    assert (m["limit"], m["used"], m["remaining"], m["override"]) == (5, 2, 3, None)
    assert m["status"] == "active" and m["period"] == "week"
    start, end = period_bounds(UsagePeriod.WEEK, datetime.now(UTC))
    assert datetime.fromisoformat(m["period_start"]) == start
    assert datetime.fromisoformat(m["period_end"]) == end


async def test_current_active_override_applies(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=5)
    await override(db_session, w.user.id, w.metric.id, 50)
    async with make_client(admin_permissions=READ) as c:
        (m,) = (await c.get(_url(w, w.user.id))).json()["metrics"]
    assert m["limit"] == 50 and m["remaining"] == 50
    assert m["override"]["limit"] == 50 and m["override"]["active"] is True


async def test_current_expired_override_falls_back(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=5)
    await override(db_session, w.user.id, w.metric.id, 50, expired=True)
    async with make_client(admin_permissions=READ) as c:
        (m,) = (await c.get(_url(w, w.user.id))).json()["metrics"]
    assert m["limit"] == 5
    assert m["override"]["active"] is False and m["override"]["expires_at"] is not None


async def test_current_zero_override_blocks(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=5)
    await override(db_session, w.user.id, w.metric.id, 0)
    async with make_client(admin_permissions=READ) as c:
        (m,) = (await c.get(_url(w, w.user.id))).json()["metrics"]
    assert (m["limit"], m["remaining"]) == (0, 0)


async def test_current_includes_archived_metric(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    await make_metric(db_session, w.app, "old-one", status="archived")
    async with make_client(admin_permissions=READ) as c:
        body = (await c.get(_url(w, w.user.id))).json()
    assert [(m["metric"], m["status"]) for m in body["metrics"]] == [
        ("ai-calls", "active"),
        ("old-one", "archived"),
    ]


async def test_current_non_member_200(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=5)
    outsider = await make_user(db_session)
    await override(db_session, outsider.id, w.metric.id, 9)
    async with make_client(admin_permissions=READ) as c:
        resp = await c.get(_url(w, outsider.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["member"] is False
    assert body["metrics"][0]["limit"] == 9


async def test_current_metric_filter(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await make_metric(db_session, w.app, "old-one", status="archived")
    async with make_client(admin_permissions=READ) as c:
        body = (await c.get(_url(w, w.user.id) + "?metric=old-one")).json()
    assert [m["metric"] for m in body["metrics"]] == ["old-one"]


async def test_current_remaining_never_negative(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=5)
    async with make_client(admin_permissions=READ) as c:
        await consume(c, w, "k1", 1)
        await db_session.execute(update(UsageCounter).values(used=9))
        (m,) = (await c.get(_url(w, w.user.id))).json()["metrics"]
    assert (m["used"], m["remaining"]) == (9, 0)


async def test_periods_paged_newest_first(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    for i in range(3):
        await _counter(db_session, w, T0 + timedelta(days=i), i + 1)
    async with make_client(admin_permissions=READ) as c:
        body = (await c.get(_url(w, w.user.id, "/periods"))).json()
        page = (await c.get(_url(w, w.user.id, "/periods") + "?limit=1&offset=1")).json()
    assert [i["used"] for i in body["items"]] == [3, 2, 1]
    assert body["total"] == 3
    assert [i["used"] for i in page["items"]] == [2]
    assert (page["limit"], page["offset"], page["total"]) == (1, 1, 3)
    first = body["items"][0]
    assert first["metric"] == "ai-calls" and first["period"] == "day"
    assert datetime.fromisoformat(first["period_end"]) - datetime.fromisoformat(
        first["period_start"]
    ) == timedelta(days=1)


async def test_periods_filters(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    other = await make_metric(db_session, w.app, "other-metric")
    for i in range(3):
        await _counter(db_session, w, T0 + timedelta(days=i), i + 1)
    await _counter(db_session, w, T0, 7, metric_id=other.id)
    base = _url(w, w.user.id, "/periods")
    f, t = (T0 + timedelta(days=1)).isoformat(), (T0 + timedelta(days=2)).isoformat()
    async with make_client(admin_permissions=READ) as c:
        by_metric = (await c.get(base, params={"metric": "other-metric"})).json()
        window = (await c.get(base, params={"metric": "ai-calls", "from": f, "to": t})).json()
    assert [i["used"] for i in by_metric["items"]] == [7]
    assert [i["used"] for i in window["items"]] == [2]  # from inclusive, to exclusive


async def test_periods_has_no_limit_field(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    w2 = await world(db_session)
    await _counter(db_session, w, T0, 1)
    await _counter(db_session, w2, T0, 99)
    stranger = await make_member(db_session, w.role)
    db_session.add(
        UsageCounter(
            app_id=w.app.id,
            user_id=stranger.id,
            metric_id=w.metric.id,
            period="day",
            period_start=T0,
            used=55,
        )
    )
    await db_session.flush()
    async with make_client(admin_permissions=READ) as c:
        body = (await c.get(_url(w, w.user.id, "/periods"))).json()
    assert body["total"] == 1
    assert set(body["items"][0]) == {"metric", "period", "period_start", "period_end", "used"}


async def test_events_paged_newest_first(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=50)
    async with make_client(admin_permissions=READ) as c:
        await consume(c, w, "e1", 2)
        await consume(c, w, "e2", 3)
        await settle(c, w, "e2", 1)
        await consume(c, w, "e3", 4)
        await refund(c, w, "e3")
        # One test transaction: `now()` is frozen, so order the rows explicitly.
        for amount, age in ((2, 3), (3, 2), (4, 1)):
            await db_session.execute(
                update(UsageEvent)
                .where(UsageEvent.user_id == w.user.id, UsageEvent.amount == amount)
                .values(created_at=T0 + timedelta(hours=3 - age))
            )
        body = (await c.get(_url(w, w.user.id, "/events"))).json()
        page = (await c.get(_url(w, w.user.id, "/events") + "?limit=2&offset=2")).json()
    assert body["total"] == 3
    assert [(i["state"], i["amount"], i["charged"]) for i in body["items"]] == [
        ("refunded", 4, 0),
        ("settled", 3, 1),
        ("consumed", 2, 2),
    ]
    assert [i["state"] for i in page["items"]] == ["consumed"]
    assert body["items"][1]["settled_amount"] == 1 and body["items"][2]["settled_at"] is None


async def test_events_filters_state(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=50)
    other = await make_metric(db_session, w.app, "other-metric", default_limit=50)
    async with make_client(admin_permissions=READ) as c:
        await consume(c, w, "e1", 2)
        await consume(c, w, "e2", 3)
        await refund(c, w, "e2")
        await consume(c, w, "e3", 1, metric=other.key)
        base = _url(w, w.user.id, "/events")
        refunded = (await c.get(base, params={"state": "refunded"})).json()
        by_metric = (await c.get(base, params={"metric": "other-metric"})).json()
        past = (await c.get(base, params={"to": (T0).isoformat()})).json()
        future = (await c.get(base, params={"from": T0.isoformat()})).json()
    assert [i["amount"] for i in refunded["items"]] == [3]
    assert [i["metric"] for i in by_metric["items"]] == ["other-metric"]
    assert past["total"] == 0 and future["total"] == 3


async def test_events_never_return_idempotency_key(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    raw_key = "secret-idem-key-123"
    async with make_client(admin_permissions=READ) as c:
        await consume(c, w, raw_key, 1)
        resp = await c.get(_url(w, w.user.id, "/events"))
    assert resp.json()["total"] == 1
    assert "idempotency_key" not in resp.text and raw_key not in resp.text
    assert set(resp.json()["items"][0]) == {
        "id",
        "metric",
        "amount",
        "settled_amount",
        "charged",
        "state",
        "period",
        "period_start",
        "period_end",
        "created_at",
        "settled_at",
        "refunded_at",
    }
    assert await db_session.get(UsageEvent, uuid.UUID(resp.json()["items"][0]["id"])) is not None


async def test_routes_401_without_token(make_client: MakeClient) -> None:
    async with make_client() as c:
        for u in _routes("shop", uuid.uuid4()):
            assert (await c.get(u)).status_code == 401


async def test_routes_401_with_app_credential(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    _, raw = await usage_key(db_session, w.app, "usage:read")
    async with make_client() as c:
        for u in _routes(w, w.user.id):
            assert (await c.get(u, headers=auth(raw))).status_code == 401


async def test_routes_403_without_read(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    async with make_client(admin_permissions=["admin:usage:write"]) as c:
        for u in _routes(w, w.user.id):
            resp = await c.get(u)
            assert resp.status_code == 403
            assert "admin:usage:read" in resp.text


async def test_routes_200_with_admin_star(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    async with make_client(admin_permissions=["admin:*"]) as c:
        for u in _routes(w, w.user.id):
            assert (await c.get(u)).status_code == 200


@pytest.mark.parametrize("route", ["", "/periods", "/events"])
@pytest.mark.parametrize("case", ["unknown_app", "admin_app", "unknown_user", "unknown_metric"])
async def test_404_matrix(
    make_client: MakeClient, db_session: AsyncSession, route: str, case: str
) -> None:
    w = await world(db_session)
    app_key, user_id, query, code = w.app.key, w.user.id, "", "not_found"
    if case == "unknown_app":
        app_key = "no-such-app"
    elif case == "admin_app":
        app_key = "admin"
    elif case == "unknown_user":
        user_id = uuid.uuid4()
    else:
        query, code = "?metric=nope-metric", "usage_metric_not_found"
    async with make_client(admin_permissions=READ) as c:
        resp = await c.get(_url(app_key, user_id, route) + query)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == code


@pytest.mark.parametrize("route", ["/periods", "/events"])
@pytest.mark.parametrize(
    "query",
    [
        "?metric=BAD KEY",
        "?from=2026-01-01T00:00:00",
        "?to=2026-01-01T00:00:00",
        "?from=2026-02-01T00:00:00Z&to=2026-01-01T00:00:00Z",
        "?limit=101",
        "?limit=0",
    ],
)
async def test_422_validation(
    make_client: MakeClient, db_session: AsyncSession, route: str, query: str
) -> None:
    w = await world(db_session)
    async with make_client(admin_permissions=READ) as c:
        resp = await c.get(_url(w, w.user.id, route) + query)
    assert resp.status_code == 422


async def test_422_bad_uuid_metric_and_state(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    async with make_client(admin_permissions=READ) as c:
        for u in _routes(w, "not-a-uuid"):
            assert (await c.get(u)).status_code == 422
        assert (await c.get(_url(w, w.user.id) + "?metric=BAD")).status_code == 422
        assert (await c.get(_url(w, w.user.id, "/events") + "?state=bogus")).status_code == 422
