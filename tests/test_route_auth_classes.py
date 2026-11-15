"""Every route belongs to exactly one auth class (R3, ADR-0008): deny by default.

A new route fails `test_every_route_is_classified` until it is either guarded with
`current_admin` + `require_permission` or added to an explicit allow-list below.
"""

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.main import create_app
from app.modules.admin_auth.deps import RequirePermission, current_admin
from tests.admin_auth_factories import bearer, make_staff, mint
from tests.auth_factories import auth_headers, make_device, make_login_app
from tests.conftest import MakeClient

V1 = "/api/v1"
PUBLIC = {
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/.well-known/jwks.json"),
    ("GET", f"{V1}/apps/{{key}}/roles"),
    ("POST", f"{V1}/auth/signup"),
    ("POST", f"{V1}/auth/login"),
    ("POST", f"{V1}/auth/verify-email"),
    ("POST", f"{V1}/auth/password/forgot"),
    ("POST", f"{V1}/auth/password/reset"),
}
COOKIE = {
    ("POST", f"{V1}/auth/logout"),
    ("POST", f"{V1}/auth/token"),
    ("POST", f"{V1}/auth/apps/{{key}}/join"),
    ("POST", f"{V1}/auth/verify-email/resend"),
    ("GET", f"{V1}/auth/accounts"),
}
DOCS = {"/openapi.json", "/docs", "/docs/oauth2-redirect"}  # Starlette routes, no dependant

BEARER = {
    ("GET", f"{V1}/organizations"): "admin:organizations:read",
    ("POST", f"{V1}/organizations"): "admin:organizations:write",
    ("GET", f"{V1}/organizations/{{org_id}}"): "admin:organizations:read",
    ("PATCH", f"{V1}/organizations/{{org_id}}"): "admin:organizations:write",
    ("DELETE", f"{V1}/organizations/{{org_id}}"): "admin:organizations:write",
    ("GET", f"{V1}/users"): "admin:users:read",
    ("GET", f"{V1}/users/{{user_id}}"): "admin:users:read",
    ("PATCH", f"{V1}/users/{{user_id}}"): "admin:users:write",
    ("GET", f"{V1}/users/{{user_id}}/role-assignments"): "admin:users:read",
    ("POST", f"{V1}/users/{{user_id}}/role-assignments"): "admin:roles:assign",
    ("DELETE", f"{V1}/users/{{user_id}}/role-assignments/{{assignment_id}}"): "admin:roles:assign",
    ("GET", f"{V1}/apps"): "admin:apps:read",
    ("POST", f"{V1}/apps"): "admin:apps:write",
    ("GET", f"{V1}/apps/{{key}}"): "admin:apps:read",
    ("PATCH", f"{V1}/apps/{{key}}"): "admin:apps:write",
    ("DELETE", f"{V1}/apps/{{key}}"): "admin:apps:write",
    ("GET", f"{V1}/apps/{{key}}/permissions"): "admin:apps:read",
    ("POST", f"{V1}/apps/{{key}}/permissions"): "admin:roles:manage",
    ("PATCH", f"{V1}/apps/{{key}}/permissions/{{permission_id}}"): "admin:roles:manage",
    ("DELETE", f"{V1}/apps/{{key}}/permissions/{{permission_id}}"): "admin:roles:manage",
    ("POST", f"{V1}/apps/{{key}}/roles"): "admin:roles:manage",
    ("PATCH", f"{V1}/apps/{{key}}/roles/{{role_id}}"): "admin:roles:manage",
    ("DELETE", f"{V1}/apps/{{key}}/roles/{{role_id}}"): "admin:roles:manage",
    ("PUT", f"{V1}/apps/{{key}}/roles/{{role_id}}/permissions"): "admin:roles:manage",
    ("GET", f"{V1}/audit-logs"): "admin:audit:read",
}
_PRODUCTION = {
    "app_env": "production",
    "trusted_proxy_cidrs": ["172.18.0.0/16"],
    "argon2_memory_kib": 65536,
    "argon2_time_cost": 3,
}


def _app() -> FastAPI:
    return create_app()  # test settings: every router mounted, docs on


def _calls(dependant: Dependant) -> Iterator[Callable[..., Any]]:
    for sub in dependant.dependencies:
        if sub.call is not None:
            yield sub.call
        yield from _calls(sub)


def _api_routes(app: FastAPI) -> dict[tuple[str, str], Dependant]:
    """(method, path) -> dependant of every APIRoute."""
    routes: dict[tuple[str, str], Dependant] = {}
    for ctx in iter_route_contexts(app.routes):
        if not isinstance(ctx.original_route, APIRoute):
            continue
        assert ctx.path is not None and ctx.methods is not None
        for method in ctx.methods:
            routes[(method, ctx.path)] = ctx.dependant
    return routes


def _other_paths(app: FastAPI) -> set[str]:
    return {
        ctx.path
        for ctx in iter_route_contexts(app.routes)
        if not isinstance(ctx.original_route, APIRoute) and ctx.path is not None
    }


def _guard(dependant: Dependant) -> tuple[bool, list[str]]:
    calls = list(_calls(dependant))
    keys = [c.key for c in calls if isinstance(c, RequirePermission)]
    return current_admin in calls, keys


def test_every_route_is_classified() -> None:
    app = _app()
    unclassified = []
    for route, dependant in _api_routes(app).items():
        has_admin, keys = _guard(dependant)
        if route not in PUBLIC | COOKIE and not (has_admin and len(keys) == 1):
            unclassified.append(route)
    assert unclassified == []
    assert _other_paths(app) <= DOCS


def test_bearer_route_permission_map() -> None:
    app = _app()
    routes = _api_routes(app)
    guarded = {r for r in routes if r not in PUBLIC | COOKIE}
    assert guarded == set(BEARER)
    paths = app.openapi()["paths"]
    for route, expected in BEARER.items():
        has_admin, keys = _guard(routes[route])
        assert has_admin, route
        assert keys == [expected], route
        method, path = route
        assert paths[path][method.lower()]["x-permission"] == expected, route


def test_allow_lists_have_no_stale_entries() -> None:
    app = _app()
    routes = _api_routes(app)
    assert set(routes) >= PUBLIC | COOKIE
    assert _other_paths(app) == DOCS


def test_public_and_cookie_routes_have_no_bearer_guard() -> None:
    routes = _api_routes(_app())
    for route in PUBLIC | COOKIE:
        assert _guard(routes[route]) == (False, []), route


def test_openapi_bearer_scheme() -> None:
    schema = _app().openapi()
    assert schema["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            route = (method.upper(), path)
            if route in BEARER:
                assert operation["security"] == [{"bearerAuth": []}], route
                assert {"401", "403"} <= set(operation["responses"]), route
            else:
                assert "bearerAuth" not in str(operation.get("security", [])), route
                assert "x-permission" not in operation, route


async def test_docs_off_in_production(make_client: MakeClient) -> None:
    app = create_app(Settings(**_PRODUCTION))  # type: ignore[arg-type]
    assert _other_paths(app) == set()
    assert set(_api_routes(app)) == set(_api_routes(_app()))  # every router still mounted
    async with make_client(**_PRODUCTION) as client:
        for path in sorted(DOCS):
            resp = await client.get(path)
            assert resp.status_code == 404, path
            assert resp.json()["error"]["code"] == "not_found", path


# --- runtime sweep -----------------------------------------------------------


def _fill(path: str) -> str:
    return path.replace("{key}", "stoktok").format_map(
        {
            name: uuid.uuid4()
            for name in ("org_id", "user_id", "assignment_id", "permission_id", "role_id")
        }
    )


async def _call(
    client: AsyncClient, method: str, path: str, headers: dict[str, str] | None = None
) -> Any:
    body = {} if method in {"POST", "PATCH", "PUT"} else None
    return await client.request(method, _fill(path), json=body, headers=headers)


@pytest.mark.parametrize(("method", "path"), list(BEARER))
async def test_every_bearer_route_401_without_token(
    client: AsyncClient, method: str, path: str
) -> None:
    resp = await _call(client, method, path)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert resp.headers["www-authenticate"].startswith("Bearer ")


@pytest.mark.parametrize(("method", "path"), list(BEARER))
async def test_every_bearer_route_401_with_cookie_only(
    client: AsyncClient, db_session: AsyncSession, method: str, path: str
) -> None:
    staff = await make_staff(db_session, "admin:*")
    raw, _, _ = await make_device(db_session, staff)
    resp = await _call(client, method, path, auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_public_routes_reachable_without_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    for path in ("/healthz", "/readyz", "/.well-known/jwks.json", f"{V1}/apps/stoktok/roles"):
        assert (await client.get(path)).status_code == 200, path
    for path in ("signup", "login", "verify-email", "password/forgot", "password/reset"):
        resp = await client.post(f"{V1}/auth/{path}", json={}, headers=auth_headers())
        assert resp.status_code != 401 or resp.json()["error"]["code"] != "unauthorized", path
        assert "www-authenticate" not in resp.headers, path


async def test_cookie_routes_not_bearer_guarded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    staff = await make_staff(db_session, "admin:*")
    token = mint(staff)
    for method, path in COOKIE:
        url = path.replace("{key}", "stoktok")
        body = {} if method == "POST" else None
        no_header = await client.request(method, url, json=body)
        assert no_header.json()["error"]["code"] == "app_header_required", path
        for headers in (auth_headers(), {**auth_headers(), **bearer(token)}):
            resp = await client.request(method, url, json=body, headers=headers)
            assert resp.status_code == 401, path
            assert resp.json()["error"]["code"] == "session_invalid", path
            assert "www-authenticate" not in resp.headers, path
