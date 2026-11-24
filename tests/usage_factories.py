"""Setup for the ADM-042 usage metering tests (flush only; the test transaction rolls back)."""

import uuid
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, Role
from app.modules.usage.models import UsageCounter, UsageEvent, UsageLimitOverride, UsageMetric
from app.modules.users.models import User
from tests.app_credential_factories import make_credential
from tests.rbac_factories import assign, make_app, make_role, make_user

V1 = "/api/v1"


class World:
    """One app with a member role, a `usage:write` key and one metric `ai-calls`."""

    app: App
    role: Role
    metric: UsageMetric
    raw: str
    key_id: str
    user: User


def app_key() -> str:
    return f"app-{uuid.uuid4().hex[:8]}"


async def make_metric(
    db: AsyncSession,
    app: App,
    key: str = "ai-calls",
    *,
    default_limit: int = 5,
    period: str = "week",
    status: str = "active",
) -> UsageMetric:
    metric = UsageMetric(
        app_id=app.id,
        key=key,
        display_name=key.title(),
        unit="call",
        default_limit=default_limit,
        period=period,
        status=status,
    )
    db.add(metric)
    await db.flush()
    await db.refresh(metric)
    return metric


async def make_member(db: AsyncSession, role: Role, **kwargs: Any) -> User:
    user = await make_user(db)
    await assign(db, user, role, **kwargs)
    return user


async def usage_key(db: AsyncSession, app: App, scope: str = "usage:write") -> tuple[str, str]:
    cred, raw = await make_credential(db, app, scope=scope)
    return cred.key_id, raw


async def world(db: AsyncSession, *, limit: int = 5, period: str = "week") -> World:
    w = World()
    w.app = await make_app(db, app_key())
    w.role = await make_role(db, w.app, f"{w.app.key}:member")
    w.metric = await make_metric(db, w.app, default_limit=limit, period=period)
    w.key_id, w.raw = await usage_key(db, w.app)
    w.user = await make_member(db, w.role)
    await db.commit()
    return w


def auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def url(w_or_key: World | str, route: str) -> str:
    key = w_or_key if isinstance(w_or_key, str) else w_or_key.app.key
    return f"{V1}/apps/{key}/usage/{route}"


async def consume(
    client: AsyncClient,
    w: World,
    key: str,
    amount: int = 1,
    *,
    user: User | None = None,
    metric: str = "ai-calls",
) -> Response:
    body = {
        "user_id": str((user or w.user).id),
        "metric": metric,
        "amount": amount,
        "idempotency_key": key,
    }
    return await client.post(url(w, "consume"), json=body, headers=auth(w.raw))


async def settle(client: AsyncClient, w: World, key: str, amount: int) -> Response:
    body = {
        "user_id": str(w.user.id),
        "metric": "ai-calls",
        "amount": amount,
        "idempotency_key": key,
    }
    return await client.post(url(w, "settle"), json=body, headers=auth(w.raw))


async def refund(client: AsyncClient, w: World, key: str) -> Response:
    body = {"user_id": str(w.user.id), "metric": "ai-calls", "idempotency_key": key}
    return await client.post(url(w, "refund"), json=body, headers=auth(w.raw))


async def counters(db: AsyncSession, user_id: uuid.UUID) -> list[UsageCounter]:
    rows = await db.scalars(
        select(UsageCounter)
        .where(UsageCounter.user_id == user_id)
        .order_by(UsageCounter.period_start)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def used(db: AsyncSession, user_id: uuid.UUID) -> int:
    return sum(c.used for c in await counters(db, user_id))


async def events(db: AsyncSession, app_id: uuid.UUID) -> list[UsageEvent]:
    rows = await db.scalars(
        select(UsageEvent)
        .where(UsageEvent.app_id == app_id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def override(
    db: AsyncSession, user_id: uuid.UUID, metric_id: uuid.UUID, limit: int, expired: bool = False
) -> None:
    db.add(
        UsageLimitOverride(
            user_id=user_id,
            metric_id=metric_id,
            limit_value=limit,
        )
    )
    await db.flush()
    if expired:
        await db.execute(
            text(
                "UPDATE usage_limit_overrides SET expires_at = now() - interval '1 hour' "
                "WHERE user_id = :u AND metric_id = :m"
            ),
            {"u": user_id, "m": metric_id},
        )
