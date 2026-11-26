"""ADM-042: credential rules and rate limits of the four usage routes (R3 class 4 (a))."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from tests.admin_auth_factories import bearer, make_staff, mint
from tests.conftest import MakeClient
from tests.rbac_factories import make_app
from tests.usage_factories import V1, World, app_key, auth, consume, usage_key, world

ROUTES = [
    ("GET", "usage/users/{uid}"),
    ("POST", "usage/consume"),
    ("POST", "usage/settle"),
    ("POST", "usage/refund"),
]


def _body(w: World) -> dict[str, object]:
    return {
        "user_id": str(w.user.id),
        "metric": "ai-calls",
        "amount": 1,
        "idempotency_key": "k",
    }


async def _call(
    client: AsyncClient, w: World, method: str, route: str, headers: dict[str, str], key: str = ""
) -> int:
    path = f"{V1}/apps/{key or w.app.key}/" + route.format(uid=w.user.id)
    if method == "GET":
        return (await client.get(path, headers=headers)).status_code
    return (await client.post(path, json=_body(w), headers=headers)).status_code


@pytest.mark.parametrize("scope", ["manifest", "releases", "org-members:read"])
@pytest.mark.parametrize(("method", "route"), ROUTES)
async def test_wrong_scope_403(
    client: AsyncClient, db_session: AsyncSession, scope: str, method: str, route: str
) -> None:
    w = await world(db_session)
    _, raw = await usage_key(db_session, w.app, scope)
    await db_session.commit()
    assert await _call(client, w, method, route, auth(raw)) == 403


@pytest.mark.parametrize(("method", "route"), ROUTES[1:])
async def test_usage_read_key_cannot_consume_403(
    client: AsyncClient, db_session: AsyncSession, method: str, route: str
) -> None:
    w = await world(db_session)
    _, raw = await usage_key(db_session, w.app, "usage:read")
    await db_session.commit()
    assert await _call(client, w, method, route, auth(raw)) == 403


@pytest.mark.parametrize(("method", "route"), ROUTES)
async def test_other_app_key_403(
    client: AsyncClient, db_session: AsyncSession, method: str, route: str
) -> None:
    w = await world(db_session)
    other = await world(db_session)
    status = await _call(client, w, method, route, auth(other.raw))
    assert status == 403


async def test_other_app_error_code(client: AsyncClient, db_session: AsyncSession) -> None:
    w, other = await world(db_session), await world(db_session)
    resp = await client.post(
        f"{V1}/apps/{w.app.key}/usage/consume", json=_body(w), headers=auth(other.raw)
    )
    assert resp.json()["error"]["code"] == "app_credential_mismatch"


@pytest.mark.parametrize(("method", "route"), ROUTES)
async def test_admin_jwt_401(
    client: AsyncClient, db_session: AsyncSession, method: str, route: str
) -> None:
    w = await world(db_session)
    staff = await make_staff(db_session, "admin:*")
    assert await _call(client, w, method, route, bearer(mint(staff))) == 401


@pytest.mark.parametrize(("method", "route"), ROUTES)
async def test_disabled_app_403(
    client: AsyncClient, db_session: AsyncSession, method: str, route: str
) -> None:
    w = await world(db_session)
    w.app.status = "disabled"
    await db_session.flush()
    assert await _call(client, w, method, route, auth(w.raw)) == 403


async def test_usage_write_key_reads(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    assert await _call(client, w, "GET", ROUTES[0][1], auth(w.raw)) == 200


async def test_per_key_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=10)
    async with make_client(usage_key_rate_limit=2) as c:
        assert (await consume(c, w, "a")).status_code == 200
        assert (await consume(c, w, "b")).status_code == 200
        resp = await consume(c, w, "c")  # the bucket is shared by the four routes
        assert resp.status_code == 429
        assert resp.headers["Retry-After"]
        assert await _call(c, w, "GET", ROUTES[0][1], auth(w.raw)) == 429


async def test_per_ip_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=10)
    async with make_client(usage_rate_limit=1) as c:
        assert (await consume(c, w, "a")).status_code == 200
        assert (await consume(c, w, "b")).status_code == 429
        # before auth: a bad credential is throttled too
        resp = await c.post(f"{V1}/apps/{w.app.key}/usage/consume", json=_body(w))
        assert resp.status_code == 429


async def test_failed_auth_ip_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    w = await world(db_session, limit=10)
    bad = auth("mmak_" + "0" * 16 + "_" + "x" * 43)
    async with make_client(usage_auth_failed_rate_limit=2) as c:
        for _ in range(2):
            assert (
                await c.post(f"{V1}/apps/{w.app.key}/usage/consume", json=_body(w), headers=bad)
            ).status_code == 401
        third = await c.post(f"{V1}/apps/{w.app.key}/usage/consume", json=_body(w), headers=bad)
        assert third.status_code == 429  # replaces the 401
        # a successful call does not count against the failed-auth bucket
        assert (await consume(c, w, "ok")).status_code == 200


async def test_failed_auth_counts_wrong_scope_and_mismatch(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    _, read_raw = await usage_key(db_session, w.app, "usage:read")
    await db_session.commit()
    async with make_client(usage_auth_failed_rate_limit=1) as c:
        assert await _call(c, w, "POST", "usage/consume", auth(read_raw)) == 403
        await db_session.refresh(w.app)  # the denied row's savepoint expired the objects
        await db_session.refresh(w.user)
        assert await _call(c, w, "POST", "usage/consume", auth(read_raw)) == 429


async def test_wrong_scope_writes_denied_row(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await world(db_session)
    _, raw = await usage_key(db_session, w.app, "usage:read")
    await db_session.commit()
    assert await _call(client, w, "POST", "usage/refund", auth(raw)) == 403
    rows = list(
        await db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "usage.refunded")
            .execution_options(populate_existing=True)
        )
    )
    assert [(r.outcome, r.changes) for r in rows] == [
        ("denied", {"reason": "app_credential_scope"})
    ]


async def test_unknown_app_path_with_valid_key_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await world(db_session)
    other = await make_app(db_session, app_key())
    await db_session.commit()
    assert other.key != w.app.key
    assert await _call(client, w, "POST", "usage/consume", auth(w.raw), key=other.key) == 403
