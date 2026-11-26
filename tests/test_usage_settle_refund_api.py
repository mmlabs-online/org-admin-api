"""ADM-042: POST .../usage/settle and .../usage/refund (usage:write app credential)."""

import logging
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.usage_factories import (
    auth,
    consume,
    counters,
    make_member,
    make_metric,
    refund,
    settle,
    url,
    used,
    world,
)


async def _age(db: AsyncSession, key: str) -> None:
    """Move the event 25 h into the past (the adjust window is 24 h)."""
    await db.execute(
        text(
            "UPDATE usage_events SET created_at = now() - interval '25 hours' "
            "WHERE idempotency_key = :k"
        ),
        {"k": key},
    )


async def _code(resp: object) -> str:
    return resp.json()["error"]["code"]  # type: ignore[attr-defined,no-any-return]


# --- settle ---------------------------------------------------------------


async def test_settle_up_charges_delta_past_limit_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=5)
    assert (await consume(client, w, "k", 4)).status_code == 200
    resp = await settle(client, w, "k", 9)  # +5 takes it past the limit: accepted, never 409
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["state"], body["amount"], body["charged"]) == ("settled", 4, 9)
    assert (body["used"], body["remaining"]) == (9, 0)
    assert await used(db_session, w.user.id) == 9


async def test_settle_down_credits_delta(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=10)
    await consume(client, w, "k", 8)
    resp = await settle(client, w, "k", 3)
    assert (resp.json()["used"], resp.json()["charged"]) == (3, 3)


async def test_settle_replay_200(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=10)
    await consume(client, w, "k", 4)
    first = await settle(client, w, "k", 6)
    again = await settle(client, w, "k", 6)
    assert again.status_code == 200
    assert again.json() == {**first.json(), "replayed": True}
    assert await used(db_session, w.user.id) == 6


async def test_settle_changed_amount_409(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=10)
    await consume(client, w, "k", 4)
    await settle(client, w, "k", 6)
    resp = await settle(client, w, "k", 7)
    assert (resp.status_code, await _code(resp)) == (409, "idempotency_key_reused")


async def test_settle_after_refund_409(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await consume(client, w, "k", 2)
    await refund(client, w, "k")
    resp = await settle(client, w, "k", 1)
    assert (resp.status_code, await _code(resp)) == (409, "usage_event_state_conflict")


async def test_settle_unknown_key_404(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    resp = await settle(client, w, "nope", 1)
    assert (resp.status_code, await _code(resp)) == (404, "usage_event_not_found")


async def test_settle_other_apps_key_404(client: AsyncClient, db_session: AsyncSession) -> None:
    w1, w2 = await world(db_session), await world(db_session)
    await consume(client, w1, "k", 1)
    assert (await settle(client, w2, "k", 1)).status_code == 404


async def test_settle_refund_mismatched_user_or_metric_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=10)
    other = await make_member(db_session, w.role)
    await make_metric(db_session, w.app, "second-metric")
    await consume(client, w, "k", 2)
    for route in ("settle", "refund"):
        for user_id, metric in ((other.id, "ai-calls"), (w.user.id, "second-metric")):
            body = {"user_id": str(user_id), "metric": metric, "idempotency_key": "k"}
            if route == "settle":
                body["amount"] = 1  # type: ignore[assignment]
            resp = await client.post(url(w, route), json=body, headers=auth(w.raw))
            assert (resp.status_code, await _code(resp)) == (409, "idempotency_key_reused")
    assert await used(db_session, w.user.id) == 2


async def test_settle_after_window_409(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await consume(client, w, "k", 2)
    await _age(db_session, "k")
    resp = await settle(client, w, "k", 1)
    assert (resp.status_code, await _code(resp)) == (409, "usage_adjust_window_closed")


async def test_settle_charged_to_original_period(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=10, period="week")
    await consume(client, w, "k", 4)
    await db_session.execute(
        text("UPDATE usage_metrics SET period = 'day' WHERE id = :m"), {"m": w.metric.id}
    )
    resp = await settle(client, w, "k", 6)
    assert resp.json()["period"] == "week"
    rows = await counters(db_session, w.user.id)
    assert [(c.period, c.used) for c in rows] == [("week", 6)]


async def test_settle_above_max_limit_422(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=10)
    await consume(client, w, "k", 4)
    await db_session.execute(
        text("UPDATE usage_counters SET used = 9007199254740991 - 1 WHERE user_id = :u"),
        {"u": w.user.id},
    )
    resp = await settle(client, w, "k", 100)
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == [{"field": "amount", "reason": "out_of_range"}]


# --- refund ---------------------------------------------------------------


async def test_refund_restores_amount(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=10)
    await consume(client, w, "a", 3)
    await consume(client, w, "b", 2)
    resp = await refund(client, w, "b")
    assert resp.status_code == 200, resp.text
    assert (resp.json()["state"], resp.json()["charged"], resp.json()["used"]) == ("refunded", 0, 3)
    assert resp.json()["replayed"] is False
    assert await used(db_session, w.user.id) == 3


async def test_refund_after_settle_credits_settled_amount(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=20)
    await consume(client, w, "a", 3)
    await consume(client, w, "b", 2)
    await settle(client, w, "b", 7)
    assert await used(db_session, w.user.id) == 10
    resp = await refund(client, w, "b")
    assert resp.json()["used"] == 3


async def test_double_refund_credits_once(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=20)
    await consume(client, w, "a", 3)
    await consume(client, w, "b", 2)
    await refund(client, w, "b")
    again = await refund(client, w, "b")
    assert again.status_code == 200
    assert again.json()["replayed"] is True
    assert await used(db_session, w.user.id) == 3


async def test_refund_never_below_zero(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=20)
    await consume(client, w, "a", 5)
    await db_session.execute(
        text("UPDATE usage_counters SET used = 1 WHERE user_id = :u"), {"u": w.user.id}
    )
    resp = await refund(client, w, "a")
    assert resp.json()["used"] == 0
    assert await used(db_session, w.user.id) == 0


async def test_refund_disabled_user_works(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await consume(client, w, "a", 2)
    w.user.status = "disabled"
    await db_session.flush()
    assert (await refund(client, w, "a")).status_code == 200


async def test_refund_archived_metric_works(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await consume(client, w, "a", 2)
    await db_session.execute(
        text("UPDATE usage_metrics SET status = 'archived' WHERE id = :m"), {"m": w.metric.id}
    )
    assert (await refund(client, w, "a")).status_code == 200


async def test_refund_unknown_key_404(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    resp = await refund(client, w, "nope")
    assert (resp.status_code, await _code(resp)) == (404, "usage_event_not_found")


async def test_refund_after_window_409(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await consume(client, w, "k", 2)
    await _age(db_session, "k")
    resp = await refund(client, w, "k")
    assert (resp.status_code, await _code(resp)) == (409, "usage_adjust_window_closed")
    assert await used(db_session, w.user.id) == 2


async def test_refund_replay_after_window_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    await consume(client, w, "k", 2)
    await refund(client, w, "k")
    await _age(db_session, "k")
    resp = await refund(client, w, "k")
    assert (resp.status_code, resp.json()["replayed"]) == (200, True)


async def test_consume_replay_after_refund_200_state_refunded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=2)
    await consume(client, w, "k", 2)
    await refund(client, w, "k")
    again = await consume(client, w, "k", 2)
    assert again.status_code == 200
    assert (again.json()["state"], again.json()["replayed"], again.json()["used"]) == (
        "refunded",
        True,
        0,
    )


async def test_refund_body_rejects_amount(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await consume(client, w, "k", 2)
    body = {"user_id": str(w.user.id), "metric": "ai-calls", "idempotency_key": "k", "amount": 1}
    resp = await client.post(url(w, "refund"), json=body, headers=auth(w.raw))
    assert resp.status_code == 422


async def test_refund_writes_info_log_without_key(
    client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    w = await world(db_session)
    idem = "idem-" + uuid.uuid4().hex
    await consume(client, w, idem, 2)
    for name in ("app", "app.modules.usage.meter_service"):
        logging.getLogger(name).disabled = False
    with caplog.at_level(logging.INFO):
        await refund(client, w, idem)
    lines = [r.getMessage() for r in caplog.records if r.name == "app.modules.usage.meter_service"]
    assert len(lines) == 1  # an app-module line was captured
    assert lines[0] == (
        f"usage_refund key_id={w.key_id} user_id={w.user.id} metric=ai-calls amount=2"
    )
    assert idem not in caplog.text
    assert w.raw not in caplog.text
    assert "@" not in caplog.text
