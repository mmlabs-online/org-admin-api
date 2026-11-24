"""ADM-042: parallel consumes on real Postgres (committed data, cleaned up by id).

Every writer has its own session and transaction, so the single-statement conditional upsert is
what keeps `used` within the limit. Rows are scoped by the ids created here and deleted at the end.
"""

import asyncio
import uuid
from typing import Any

from sqlalchemy import delete, func, select

from app.core.config import Settings
from app.db.session import SessionLocal
from app.modules.app_credentials.service import AuthenticatedApp
from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.usage.meter_schemas import UsageConsume
from app.modules.usage.meter_service import UsageMeterService
from app.modules.usage.models import UsageCounter, UsageEvent, UsageLimitOverride, UsageMetric
from app.modules.users.models import User
from tests.rbac_factories import assign, make_app, make_role, make_user
from tests.test_login_concurrency import TIMEOUT
from tests.usage_factories import make_metric


class Env:
    caller: AuthenticatedApp
    app_id: uuid.UUID
    user_id: uuid.UUID
    metric_id: uuid.UUID
    role_id: uuid.UUID


async def _setup(limit: int, prefill: int = 0) -> Env:
    env = Env()
    async with SessionLocal() as s:
        app = await make_app(s, f"race-{uuid.uuid4().hex[:8]}")
        role = await make_role(s, app, f"{app.key}:member")
        user = await make_user(s)
        await assign(s, user, role)
        metric = await make_metric(s, app, default_limit=limit)
        await s.commit()
        env.app_id, env.user_id, env.metric_id, env.role_id = app.id, user.id, metric.id, role.id
        env.caller = AuthenticatedApp(app.id, "0" * 16, app.id, app.key, "usage:write")
    if prefill:
        await _consume(env, "prefill", prefill)
    return env


async def _cleanup(env: Env) -> None:
    async with SessionLocal() as s:
        await s.execute(delete(UsageEvent).where(UsageEvent.app_id == env.app_id))
        await s.execute(delete(UsageCounter).where(UsageCounter.app_id == env.app_id))
        await s.execute(
            delete(UsageLimitOverride).where(UsageLimitOverride.metric_id == env.metric_id)
        )
        await s.execute(delete(UsageMetric).where(UsageMetric.id == env.metric_id))
        await s.execute(delete(RoleAssignment).where(RoleAssignment.user_id == env.user_id))
        await s.execute(delete(Role).where(Role.id == env.role_id))
        await s.execute(delete(User).where(User.id == env.user_id))
        await s.execute(delete(App).where(App.id == env.app_id))
        await s.commit()


async def _consume(env: Env, key: str, amount: int = 1) -> Any:
    body = UsageConsume.model_validate(
        {
            "user_id": str(env.user_id),
            "metric": "ai-calls",
            "amount": amount,
            "idempotency_key": key,
        }
    )
    async with SessionLocal() as s:
        try:
            result = await UsageMeterService(s, Settings()).consume(env.caller, body)
        except Exception as exc:  # the refusal rolls back, like `get_session`
            await s.rollback()
            return exc
        await s.commit()
        return result


async def _state(env: Env) -> tuple[int, int]:
    async with SessionLocal() as s:
        used = await s.scalar(
            select(func.coalesce(func.sum(UsageCounter.used), 0)).where(
                UsageCounter.user_id == env.user_id
            )
        )
        events = await s.scalar(
            select(func.count()).select_from(UsageEvent).where(UsageEvent.app_id == env.app_id)
        )
        return int(used or 0), int(events or 0)


def _accepted(results: list[Any]) -> int:
    return sum(1 for r in results if not isinstance(r, Exception))


async def test_parallel_consumes_on_last_unit() -> None:
    env = await _setup(limit=5, prefill=4)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_consume(env, f"k{i}") for i in range(8))), TIMEOUT
        )
        assert _accepted(results) == 1
        assert all(
            type(r).__name__ == "UsageLimitExceededError"
            for r in results
            if isinstance(r, Exception)
        )
        assert await _state(env) == (5, 2)  # `used == 5`; refused consumes left no event
    finally:
        await _cleanup(env)


async def test_parallel_first_consumes_fresh_counter() -> None:
    env = await _setup(limit=3)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_consume(env, f"k{i}") for i in range(10))), TIMEOUT
        )
        assert _accepted(results) == 3
        assert await _state(env) == (3, 3)
    finally:
        await _cleanup(env)


async def test_parallel_same_key_charges_once() -> None:
    env = await _setup(limit=10)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_consume(env, "same", 2) for _ in range(6))), TIMEOUT
        )
        assert _accepted(results) == 6  # every retry gets the first result
        assert sorted(r.replayed for r in results) == [False] + [True] * 5
        assert await _state(env) == (2, 1)
    finally:
        await _cleanup(env)
