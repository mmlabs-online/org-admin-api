import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.main import create_app
from app.modules.auth.deps import get_signup_hook, get_turnstile_verifier
from app.modules.users.models import User
from tests.auth_factories import (
    LOGIN,
    LOGOUT,
    ORIGIN,
    auth_headers,
    login_body,
    make_device,
    make_login_app,
    make_login_user,
)
from tests.conftest import MakeClient
from tests.fakes import FakeTurnstileVerifier

_PROD = {
    "app_env": "production",
    "trusted_proxy_cidrs": ["172.18.0.0/16"],
    "argon2_memory_kib": 19456,
    "argon2_time_cost": 2,
}


def _code(resp_json: dict[str, dict[str, str]]) -> str:
    return resp_json["error"]["code"]


@pytest.mark.parametrize("app_key", [None, "", "BAD!", "x"])
async def test_missing_app_header_403(
    client: AsyncClient, db_session: AsyncSession, app_key: str | None
) -> None:
    await make_login_app(db_session)
    user = await make_login_user(db_session)
    resp = await client.post(LOGIN, json=login_body(user), headers=auth_headers(app_key=app_key))
    assert resp.status_code == 403
    assert _code(resp.json()) == "app_header_required"


@pytest.mark.parametrize("disabled", [False, True], ids=["unknown", "disabled"])
async def test_unknown_or_disabled_app_403(
    client: AsyncClient, db_session: AsyncSession, disabled: bool
) -> None:
    if disabled:
        await make_login_app(db_session, status="disabled")
    user = await make_login_user(db_session)
    resp = await client.post(LOGIN, json=login_body(user), headers=auth_headers())
    assert resp.status_code == 403
    assert _code(resp.json()) == "origin_not_allowed"


@pytest.mark.parametrize("origin", [None, "null", "not an origin", "https://evil.example.com"])
async def test_missing_or_null_origin_403(
    client: AsyncClient, db_session: AsyncSession, origin: str | None
) -> None:
    await make_login_app(db_session)
    user = await make_login_user(db_session)
    resp = await client.post(LOGIN, json=login_body(user), headers=auth_headers(origin=origin))
    assert resp.status_code == 403
    assert _code(resp.json()) == "origin_not_allowed"


async def test_origin_of_other_app_403(client: AsyncClient, db_session: AsyncSession) -> None:
    await make_login_app(db_session)
    await make_login_app(db_session, "calechip", origins=["https://calechip.mmlabs.online"])
    user = await make_login_user(db_session)
    resp = await client.post(
        LOGIN,
        json=login_body(user),
        headers=auth_headers(app_key="calechip", origin=ORIGIN),
    )
    assert resp.status_code == 403
    assert _code(resp.json()) == "origin_not_allowed"


async def test_origin_is_normalized(client: AsyncClient, db_session: AsyncSession) -> None:
    await make_login_app(db_session)
    user = await make_login_user(db_session)
    resp = await client.post(
        LOGIN,
        json=login_body(user),
        headers=auth_headers(origin="HTTPS://Stoktok.MMLABS.online:443"),
    )
    assert resp.status_code == 200, resp.text


async def test_loopback_origin_ignored_in_production(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session, origins=[ORIGIN, "http://localhost:3000"])
    user = await make_login_user(db_session)
    body = login_body(user, "wrong password entirely")
    local = auth_headers(origin="http://localhost:3000")
    async with make_client() as client:  # test env: loopback allowed
        resp = await client.post(LOGIN, json=body, headers=local)
        assert resp.status_code == 401
    async with make_client(**_PROD) as client:
        resp = await client.post(LOGIN, json=body, headers=local)
        assert resp.status_code == 403
        assert _code(resp.json()) == "origin_not_allowed"
        resp = await client.post(LOGIN, json=body, headers=auth_headers())
        assert resp.status_code == 401  # the registered https origin still works


async def test_logout_checks_origin(client: AsyncClient, db_session: AsyncSession) -> None:
    await make_login_app(db_session)
    user = await make_login_user(db_session)
    raw, _, _ = await make_device(db_session, user)
    body = {"all": True}
    resp = await client.post(LOGOUT, json=body, headers=auth_headers(app_key=None, cookie=raw))
    assert resp.status_code == 403
    assert _code(resp.json()) == "app_header_required"
    resp = await client.post(
        LOGOUT, json=body, headers=auth_headers(origin="https://other.mmlabs.online", cookie=raw)
    )
    assert resp.status_code == 403
    assert _code(resp.json()) == "origin_not_allowed"
    resp = await client.post(LOGOUT, json=body, headers=auth_headers(cookie=raw))
    assert resp.status_code == 204


async def test_signup_403_when_signup_hook_overridden(
    db_session: AsyncSession, fake_turnstile: FakeTurnstileVerifier
) -> None:
    """The route-level `AppContextDep` keeps the 403 if the email hook dependency is replaced."""
    await make_login_app(db_session)
    calls: list[uuid.UUID] = []

    async def recorder(user_id: uuid.UUID, session: AsyncSession) -> None:
        calls.append(user_id)

    async def _session() -> Any:
        yield db_session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_turnstile_verifier] = lambda: fake_turnstile
    app.dependency_overrides[get_signup_hook] = lambda: recorder
    body = {
        "email": "dana@example.com",
        "password": "correct horse battery staple",
        "turnstile_token": "pass",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        missing = await c.post("/api/v1/auth/signup", json=body, headers={})
        foreign = await c.post(
            "/api/v1/auth/signup",
            json=body,
            headers=auth_headers(origin="https://evil.example.com"),
        )
    assert missing.status_code == 403
    assert _code(missing.json()) == "app_header_required"
    assert foreign.status_code == 403
    assert _code(foreign.json()) == "origin_not_allowed"
    assert calls == []
    assert fake_turnstile.calls == []
    assert await db_session.scalar(select(func.count()).select_from(User)) == 0
