"""ADM-042: GET /api/v1/apps/{key}/usage/users/{user_id} (usage:read or usage:write)."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.usage_factories import (
    V1,
    auth,
    consume,
    make_member,
    make_metric,
    override,
    usage_key,
    world,
)


def _url(w: object, user_id: object, query: str = "") -> str:
    return f"{V1}/apps/{w.app.key}/usage/users/{user_id}{query}"  # type: ignore[attr-defined]


async def _get(client: AsyncClient, w: object, user_id: object, query: str = "") -> object:
    return await client.get(_url(w, user_id, query), headers=auth(w.raw))  # type: ignore[attr-defined]


async def test_read_all_active_metrics(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=5)
    await make_metric(db_session, w.app, "aa-first", default_limit=9, period="day")
    await consume(client, w, "k", 2)
    resp = await _get(client, w, w.user.id)
    assert resp.status_code == 200, resp.text  # type: ignore[attr-defined]
    body = resp.json()  # type: ignore[attr-defined]
    assert body["user_id"] == str(w.user.id)
    assert [m["metric"] for m in body["metrics"]] == ["aa-first", "ai-calls"]
    first, calls = body["metrics"]
    assert (first["limit"], first["used"], first["remaining"], first["period"]) == (9, 0, 9, "day")
    assert (calls["limit"], calls["used"], calls["remaining"]) == (5, 2, 3)
    assert calls["display_name"] == "Ai-Calls" and calls["unit"] == "call"
    assert calls["period_start"] < calls["period_end"]
    assert resp.headers["Cache-Control"] == "no-store"  # type: ignore[attr-defined]
    assert "email" not in resp.text  # type: ignore[attr-defined]


async def test_read_archived_metric_omitted(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await make_metric(db_session, w.app, "old-one", status="archived")
    body = (await _get(client, w, w.user.id)).json()  # type: ignore[attr-defined]
    assert [m["metric"] for m in body["metrics"]] == ["ai-calls"]


async def test_read_metric_filter(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await make_metric(db_session, w.app, "aa-first")
    body = (await _get(client, w, w.user.id, "?metric=ai-calls")).json()  # type: ignore[attr-defined]
    assert [m["metric"] for m in body["metrics"]] == ["ai-calls"]


async def test_read_unknown_metric_404(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await make_metric(db_session, w.app, "old-one", status="archived")
    for metric in ("nope-metric", "old-one"):
        resp = await _get(client, w, w.user.id, f"?metric={metric}")
        assert resp.status_code == 404  # type: ignore[attr-defined]
        assert resp.json()["error"]["code"] == "usage_metric_not_found"  # type: ignore[attr-defined]
    assert (await _get(client, w, w.user.id, "?metric=BAD")).status_code == 422  # type: ignore[attr-defined]


async def test_read_no_counter_used_zero(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=7)
    m = (await _get(client, w, w.user.id)).json()["metrics"][0]  # type: ignore[attr-defined]
    assert (m["used"], m["remaining"], m["limit"]) == (0, 7, 7)


async def test_read_override_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=5)
    await override(db_session, w.user.id, w.metric.id, 50)
    m = (await _get(client, w, w.user.id)).json()["metrics"][0]  # type: ignore[attr-defined]
    assert m["limit"] == 50


async def test_read_remaining_clamped_after_overage(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=5)
    await consume(client, w, "k", 4)
    from tests.usage_factories import settle

    await settle(client, w, "k", 9)
    m = (await _get(client, w, w.user.id)).json()["metrics"][0]  # type: ignore[attr-defined]
    assert (m["used"], m["remaining"]) == (9, 0)


async def test_read_non_member_403(client: AsyncClient, db_session: AsyncSession) -> None:
    import uuid

    w = await world(db_session)
    for uid in (uuid.uuid4(),):
        resp = await _get(client, w, uid)
        assert resp.status_code == 403  # type: ignore[attr-defined]
        assert resp.json()["error"]["code"] == "usage_user_denied"  # type: ignore[attr-defined]
    other_app_user = (await world(db_session)).user
    assert (await _get(client, w, other_app_user.id)).status_code == 403  # type: ignore[attr-defined]


async def test_read_invalid_user_id_422(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    assert (await _get(client, w, "not-a-uuid")).status_code == 422  # type: ignore[attr-defined]


async def test_read_with_usage_read_key(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    _, raw = await usage_key(db_session, w.app, "usage:read")
    member = await make_member(db_session, w.role)
    await db_session.commit()
    resp = await client.get(_url(w, member.id), headers=auth(raw))
    assert resp.status_code == 200
