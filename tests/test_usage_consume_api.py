"""ADM-042: POST /api/v1/apps/{key}/usage/consume (usage:write app credential)."""

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage import meter_repository
from tests.conftest import MakeClient
from tests.rbac_factories import make_app, make_org, make_user
from tests.usage_factories import (
    app_key,
    auth,
    consume,
    counters,
    events,
    make_member,
    make_metric,
    override,
    url,
    used,
    world,
)


async def test_consume_within_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=5)
    resp = await consume(client, w, "k1", 2)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == str(w.user.id)
    assert body["metric"] == "ai-calls"
    assert (body["amount"], body["state"], body["charged"]) == (2, "consumed", 2)
    assert (body["limit"], body["used"], body["remaining"]) == (5, 2, 3)
    assert body["replayed"] is False
    assert body["period"] == "week"
    assert await used(db_session, w.user.id) == 2


async def test_consume_last_unit_then_409(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=2)
    assert (await consume(client, w, "a", 1)).status_code == 200
    assert (await consume(client, w, "b", 1)).status_code == 200
    resp = await consume(client, w, "c", 1)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "usage_limit_exceeded"


async def test_consume_over_limit_409_nothing_consumed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=5)
    assert (await consume(client, w, "a", 4)).status_code == 200
    resp = await consume(client, w, "b", 2)
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == [
        {"field": "amount", "reason": "over_limit", "limit": 5, "used": 4, "remaining": 1}
    ]
    assert await used(db_session, w.user.id) == 4
    assert [e.idempotency_key for e in await events(db_session, w.app.id)] == ["a"]


async def test_consume_amount_above_limit_on_fresh_counter_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=3)
    resp = await consume(client, w, "a", 4)
    assert resp.status_code == 409
    assert await counters(db_session, w.user.id) == []
    assert await events(db_session, w.app.id) == []


async def test_limit_zero_refuses(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=0)
    assert (await consume(client, w, "a", 1)).status_code == 409


async def test_override_raises_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=1)
    await override(db_session, w.user.id, w.metric.id, 10)
    resp = await consume(client, w, "a", 6)
    assert resp.status_code == 200
    assert resp.json()["limit"] == 10


async def test_expired_override_ignored(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=1)
    await override(db_session, w.user.id, w.metric.id, 10, expired=True)
    assert (await consume(client, w, "a", 6)).status_code == 409


async def test_refused_consume_retry_succeeds_after_grant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=1)
    assert (await consume(client, w, "a", 3)).status_code == 409
    await override(db_session, w.user.id, w.metric.id, 5)
    resp = await consume(client, w, "a", 3)  # same key: the refusal was not stored
    assert resp.status_code == 200
    assert resp.json()["replayed"] is False


async def test_replay_returns_first_result(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=5)
    first = await consume(client, w, "k", 2)
    again = await consume(client, w, "k", 2)
    assert again.status_code == 200
    assert again.json() == {**first.json(), "replayed": True}
    assert await used(db_session, w.user.id) == 2  # charged once


async def test_replay_after_limit_reached_still_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=2)
    assert (await consume(client, w, "k", 2)).status_code == 200
    again = await consume(client, w, "k", 2)
    assert again.status_code == 200
    assert (again.json()["used"], again.json()["remaining"]) == (2, 0)


async def test_replay_changed_amount_409(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=5)
    await consume(client, w, "k", 2)
    resp = await consume(client, w, "k", 3)
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "idempotency_key_reused")


async def test_replay_changed_user_409(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=5)
    other = await make_member(db_session, w.role)
    await consume(client, w, "k", 2)
    resp = await consume(client, w, "k", 2, user=other)
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "idempotency_key_reused")


async def test_replay_changed_metric_409(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=5)
    await make_metric(db_session, w.app, "other-metric", default_limit=5)
    await consume(client, w, "k", 2)
    resp = await consume(client, w, "k", 2, metric="other-metric")
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "idempotency_key_reused")


async def test_same_key_two_apps_independent(client: AsyncClient, db_session: AsyncSession) -> None:
    w1 = await world(db_session, limit=5)
    w2 = await world(db_session, limit=5)
    assert (await consume(client, w1, "same", 1)).json()["replayed"] is False
    assert (await consume(client, w2, "same", 1)).json()["replayed"] is False


async def test_user_denied_one_code(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    non_member = await make_user(db_session)
    disabled = await make_member(db_session, w.role)
    disabled.status = "disabled"
    expired = await make_member(
        db_session, w.role, expires_at=datetime.now(UTC) - timedelta(days=1)
    )
    await db_session.commit()
    bodies = []
    for uid in (uuid.uuid4(), non_member.id, disabled.id, expired.id):
        body = {"user_id": str(uid), "metric": "ai-calls", "amount": 1, "idempotency_key": "k"}
        resp = await client.post(url(w, "consume"), json=body, headers=auth(w.raw))
        assert resp.status_code == 403, resp.text
        bodies.append(resp.json())
    assert all(b["error"]["code"] == "usage_user_denied" for b in bodies)
    assert all(b == bodies[0] for b in bodies)


async def test_org_scoped_member_allowed(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    org = await make_org(db_session)
    user = await make_member(db_session, w.role, org_id=org.id)
    await db_session.commit()
    assert (await consume(client, w, "k", 1, user=user)).status_code == 200


async def test_unverified_member_allowed(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    assert w.user.email_verified_at is None
    assert (await consume(client, w, "k", 1)).status_code == 200


async def test_unknown_metric_404(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    resp = await consume(client, w, "k", 1, metric="nope-metric")
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "usage_metric_not_found")


async def test_archived_metric_404(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    await make_metric(db_session, w.app, "old-metric", status="archived")
    resp = await consume(client, w, "k", 1, metric="old-metric")
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "usage_metric_not_found")


async def test_other_apps_metric_404(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    other = await make_app(db_session, app_key())
    await make_metric(db_session, other, "theirs-only")
    resp = await consume(client, w, "k", 1, metric="theirs-only")
    assert resp.status_code == 404


@pytest.mark.parametrize("amount", [0, -1, 1.5, "2", True, None])
async def test_amount_zero_negative_422(
    client: AsyncClient, db_session: AsyncSession, amount: object
) -> None:
    w = await world(db_session)
    body = {
        "user_id": str(w.user.id),
        "metric": "ai-calls",
        "amount": amount,
        "idempotency_key": "k",
    }
    resp = await client.post(url(w, "consume"), json=body, headers=auth(w.raw))
    assert resp.status_code == 422


async def test_amount_over_config_max_422(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session, limit=100)
    async with make_client(usage_max_amount_per_call=10) as c:
        assert (await consume(c, w, "a", 10)).status_code == 200
        resp = await consume(c, w, "b", 11)
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == [{"field": "amount", "reason": "too_large"}]


@pytest.mark.parametrize("key", ["has space", "x" * 129, "", "naïve", "tab\t"])
async def test_idempotency_key_space_or_129_chars_422(
    client: AsyncClient, db_session: AsyncSession, key: str
) -> None:
    w = await world(db_session)
    assert (await consume(client, w, key, 1)).status_code == 422


async def test_idempotency_key_128_chars_ok(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    assert (await consume(client, w, "x" * 128, 1)).status_code == 200


@pytest.mark.parametrize("extra", ["on_behalf_of", "occurred_at"])
async def test_on_behalf_of_field_422(
    client: AsyncClient, db_session: AsyncSession, extra: str
) -> None:
    w = await world(db_session)
    body = {
        "user_id": str(w.user.id),
        "metric": "ai-calls",
        "amount": 1,
        "idempotency_key": "k",
        extra: "x",
    }
    resp = await client.post(url(w, "consume"), json=body, headers=auth(w.raw))
    assert resp.status_code == 422


async def _chunks() -> AsyncIterator[bytes]:
    yield b"{}"


async def test_body_411_413_415(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    endpoint = url(w, "consume")
    no_length = await client.post(
        endpoint,
        content=_chunks(),  # chunked: no Content-Length
        headers={**auth(w.raw), "Content-Type": "application/json"},
    )
    assert no_length.status_code == 411
    big = await client.post(
        endpoint,
        content=b"{" + b" " * 4096 + b"}",
        headers={**auth(w.raw), "Content-Type": "application/json"},
    )
    assert big.status_code == 413
    text_plain = await client.post(
        endpoint, content=b"{}", headers={**auth(w.raw), "Content-Type": "text/plain"}
    )
    assert text_plain.status_code == 415


async def test_period_change_new_bucket(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=2, period="week")
    assert (await consume(client, w, "a", 2)).status_code == 200
    assert (await consume(client, w, "b", 1)).status_code == 409
    await db_session.execute(
        text("UPDATE usage_metrics SET period = 'day' WHERE id = :m"), {"m": w.metric.id}
    )
    resp = await consume(client, w, "c", 1)  # a different period kind: a fresh bucket
    assert resp.status_code == 200
    assert resp.json()["period"] == "day"
    assert len(await counters(db_session, w.user.id)) == 2


async def test_cache_control_no_store(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    assert (await consume(client, w, "k", 1)).headers["Cache-Control"] == "no-store"


async def test_idempotency_key_never_logged(
    client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    w = await world(db_session)
    secret_key = "idem-" + uuid.uuid4().hex
    # Other tests may have disabled loggers (alembic `fileConfig`): re-enable ours.
    for name in ("app", "app.modules.usage.meter_service"):
        logging.getLogger(name).disabled = False
    other = await make_member(db_session, w.role)
    await db_session.commit()
    with caplog.at_level(logging.DEBUG):
        await consume(client, w, secret_key, 1, user=other)
        denied = {
            "user_id": str(uuid.uuid4()),
            "metric": "ai-calls",
            "amount": 1,
            "idempotency_key": secret_key + "-denied",
        }
        await client.post(url(w, "consume"), json=denied, headers=auth(w.raw))
    app_lines = [r for r in caplog.records if r.name.startswith("app.modules.usage")]
    assert any("usage_denied" in r.getMessage() for r in app_lines)  # the capture works
    assert secret_key not in caplog.text
    assert w.raw not in caplog.text


async def test_lock_timeout_set_local(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await world(db_session)
    seen: list[str | None] = []
    real = meter_repository.UsageMeterRepository.find_metric

    async def spy(self: meter_repository.UsageMeterRepository, *a: object, **k: object):  # type: ignore[no-untyped-def]
        seen.append(await self.session.scalar(text("SHOW lock_timeout")))
        return await real(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(meter_repository.UsageMeterRepository, "find_metric", spy)
    assert (await consume(client, w, "k", 1)).status_code == 200
    assert seen == ["2s"]
