"""Dynamic CORS on the real app: registry, invalidation, identity endpoints (USR-03e, ADR-0006)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac import origin_registry
from app.modules.rbac.origin_registry import AppOriginRegistry
from app.modules.rbac.repository import RbacRepository
from tests.auth_factories import (
    LOGIN,
    ORIGIN,
    auth_headers,
    login_body,
    make_login_app,
    make_login_user,
)
from tests.conftest import MakeClient

TOKEN = "/api/v1/auth/token"
OTHER = "https://calechip.mmlabs.online"
EVIL = "https://evil.mmlabs.online"
LOCAL = "http://localhost:3000"
_PROD = {
    "app_env": "production",
    "trusted_proxy_cidrs": ["172.18.0.0/16"],
    "argon2_memory_kib": 19456,
    "argon2_time_cost": 2,
}


def _preflight(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-mm-app",
    }


def _echoed(resp: Response, origin: str = ORIGIN) -> bool:
    return bool(
        resp.headers.get("access-control-allow-origin") == origin
        and resp.headers.get("access-control-allow-credentials") == "true"
    )


async def _probe(client: AsyncClient, origin: str) -> Response:
    """A cheap in-scope request (403 from the CSRF check is fine: only headers matter)."""
    return await client.post(TOKEN, json={}, headers={"Origin": origin})


# --- identity endpoints --------------------------------------------------------


async def test_preflight_token_from_registered_origin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    resp = await client.options(TOKEN, headers=_preflight(ORIGIN))
    assert resp.status_code == 204
    assert _echoed(resp)
    assert resp.headers["access-control-allow-headers"] == "Content-Type, X-MM-App"
    resp = await client.options(TOKEN, headers=_preflight(EVIL))
    assert resp.status_code == 204
    assert "access-control-allow-origin" not in resp.headers


async def test_login_cross_origin_credentialed_echo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    user = await make_login_user(db_session)
    resp = await client.post(LOGIN, json=login_body(user), headers=auth_headers())
    assert resp.status_code == 200, resp.text
    assert _echoed(resp)
    assert "Origin" in resp.headers["vary"]
    assert resp.headers["cache-control"] == "no-store"


async def test_error_envelopes_readable_cross_origin(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    await make_login_app(db_session, "calechip", origins=[OTHER])
    user = await make_login_user(db_session)
    async with make_client(login_rate_limit=3) as client:
        wrong = await client.post(
            LOGIN, json=login_body(user, "wrong password entirely"), headers=auth_headers()
        )
        other_app = await client.post(
            LOGIN, json=login_body(user), headers=auth_headers(app_key="calechip")
        )
        invalid = await client.post(LOGIN, json={"email": 1}, headers=auth_headers())
        limited = await client.post(LOGIN, json=login_body(user), headers=auth_headers())
    for resp, status, code in (
        (wrong, 401, "invalid_credentials"),
        (other_app, 403, "origin_not_allowed"),
        (invalid, 422, None),
        (limited, 429, "rate_limited"),
    ):
        assert resp.status_code == status, resp.text
        assert _echoed(resp)
        assert resp.headers["cache-control"] == "no-store"
        assert "error" in resp.json()
        if code is not None:
            assert resp.json()["error"]["code"] == code
    assert limited.headers["retry-after"]
    assert limited.headers["access-control-expose-headers"] == "Retry-After"


async def test_disabled_app_origin_not_echoed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    await make_login_app(db_session, "calechip", origins=[OTHER], status="disabled")
    assert _echoed(await _probe(client, ORIGIN))
    resp = await _probe(client, OTHER)
    assert "access-control-allow-origin" not in resp.headers
    assert "Origin" in resp.headers["vary"]


async def test_loopback_origin_not_echoed_in_production(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session, origins=[ORIGIN, LOCAL])
    async with make_client() as client:  # test env: loopback allowed
        assert _echoed(await _probe(client, LOCAL), LOCAL)
    async with make_client(**_PROD) as client:
        assert "access-control-allow-origin" not in (await _probe(client, LOCAL)).headers
        assert _echoed(await _probe(client, ORIGIN))  # the https origin still works


async def test_role_catalog_and_admin_paths_have_no_cors(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    async with make_client(admin_rbac_api_enabled=True) as client:
        for path in ("/api/v1/apps/stoktok/roles", "/api/v1/apps"):
            resp = await client.get(path, headers={"Origin": ORIGIN})
            assert resp.status_code == 200, path
            assert "access-control-allow-origin" not in resp.headers
            assert "origin" not in resp.headers.get("vary", "").lower()


# --- registry --------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@asynccontextmanager
async def _no_session() -> AsyncIterator[AsyncSession]:
    yield None  # type: ignore[misc]  # the loader is patched; the session is unused


def _patch_loader(
    monkeypatch: pytest.MonkeyPatch, origins: list[list[str]], *, gate: asyncio.Event | None = None
) -> list[int]:
    calls: list[int] = []

    async def loader(self: RbacRepository) -> list[list[str]]:
        calls.append(1)
        if gate is not None:
            await gate.wait()
        return origins

    monkeypatch.setattr(RbacRepository, "active_app_origins", loader)
    return calls


async def test_registry_ttl_and_single_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_loader(monkeypatch, [[ORIGIN]])
    clock = _Clock()
    registry = AppOriginRegistry(_no_session, "test", clock=clock)
    results = await asyncio.gather(*(registry.is_allowed(ORIGIN) for _ in range(10)))
    assert all(results)
    assert len(calls) == 1
    clock.now += 59
    assert await registry.is_allowed(ORIGIN)
    assert not await registry.is_allowed(EVIL)
    assert len(calls) == 1
    clock.now += 1
    assert await registry.is_allowed(ORIGIN)
    assert len(calls) == 2


async def test_registry_fails_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _patch_loader(monkeypatch, [[ORIGIN]])
    clock = _Clock()
    registry = AppOriginRegistry(_no_session, "test", clock=clock)
    assert await registry.is_allowed(ORIGIN)
    secret = "postgresql://user:hunter2@db/org_admin"

    async def broken(self: RbacRepository) -> list[list[str]]:
        calls.append(1)
        raise RuntimeError(secret)

    monkeypatch.setattr(RbacRepository, "active_app_origins", broken)
    # Alembic's `fileConfig` (session fixture) disables loggers created before it ran.
    monkeypatch.setattr(origin_registry.logger, "disabled", False)
    clock.now += 60
    with caplog.at_level(logging.WARNING):
        assert not await registry.is_allowed(ORIGIN)
    assert len(calls) == 2
    assert "cors_origins_refresh_failed error=RuntimeError" in caplog.text
    assert "hunter2" not in caplog.text
    clock.now += 4.9
    assert not await registry.is_allowed(ORIGIN)
    assert len(calls) == 2  # no reload within the backoff
    _patch_loader(monkeypatch, [[ORIGIN]])
    clock.now += 0.1
    assert await registry.is_allowed(ORIGIN)


async def test_registry_invalidation_during_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = asyncio.Event()
    calls = _patch_loader(monkeypatch, [[ORIGIN]], gate=gate)
    registry = AppOriginRegistry(_no_session, "test", clock=_Clock())
    pending = asyncio.create_task(registry.is_allowed(ORIGIN))
    await asyncio.sleep(0)
    assert len(calls) == 1
    registry.invalidate()
    gate.set()
    await pending
    await registry.is_allowed(ORIGIN)
    assert len(calls) == 2  # the in-flight result was not trusted as fresh
    await registry.is_allowed(ORIGIN)
    assert len(calls) == 2


async def test_invalidated_after_commit_not_on_rollback(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    calls = _patch_loader(monkeypatch, [[ORIGIN]])
    registry = AppOriginRegistry(_no_session, "test", clock=_Clock())
    await registry.is_allowed(ORIGIN)
    repo = RbacRepository(db_session)
    repo.mark_apps_changed()
    await db_session.rollback()
    await registry.is_allowed(ORIGIN)
    assert len(calls) == 1
    repo.mark_apps_changed()
    await db_session.commit()
    await registry.is_allowed(ORIGIN)
    assert len(calls) == 2


async def test_app_patch_takes_effect_before_ttl(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    await db_session.commit()
    async with make_strict_client(admin_rbac_api_enabled=True) as client:
        assert _echoed(await _probe(client, ORIGIN))  # cache primed
        resp = await client.patch("/api/v1/apps/stoktok", json={"origins": [OTHER]})
        assert resp.status_code == 200, resp.text
        assert "access-control-allow-origin" not in (await _probe(client, ORIGIN)).headers
        assert _echoed(await _probe(client, OTHER), OTHER)
        resp = await client.patch("/api/v1/apps/stoktok", json={"status": "disabled"})
        assert resp.status_code == 200, resp.text
        assert "access-control-allow-origin" not in (await _probe(client, OTHER)).headers
