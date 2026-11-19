"""Non-credentialed CORS on the Bearer admin routes, on the real app (ADM-012, ADR-0011).

Origins come only from `apps[admin].origins`; tokens are real (`mint`), no `current_admin`
override.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Headers, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.schemas import AdminOriginsSet
from app.modules.rbac.service import AppService
from tests.admin_auth_factories import bearer, make_staff, mint
from tests.auth_factories import ORIGIN as STOKTOK_ORIGIN
from tests.auth_factories import auth_headers, make_device, make_login_app
from tests.conftest import MakeClient
from tests.test_route_auth_classes import BEARER, _fill

ADMIN_ORIGIN = "https://admin-portal.mmlabs.online"
NEW_ORIGIN = "https://admin-portal2.mmlabs.online"
LOCAL = "http://localhost:5173"
APPS = "/api/v1/apps"
ORGS = "/api/v1/organizations"
TOKEN = "/api/v1/auth/token"
EXPOSE = (
    "Retry-After, ETag, X-Request-ID, Content-Disposition, X-Export-Truncated, X-Export-Row-Count"
)
PREFLIGHT_VARY = "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"
_PROD = {
    "app_env": "production",
    "trusted_proxy_cidrs": ["172.18.0.0/16"],
    "argon2_memory_kib": 19456,
    "argon2_time_cost": 2,
}


async def _set_admin_origins(session: AsyncSession, *origins: str) -> None:
    await AppService(session).set_admin_origins(
        AdminOriginsSet(origins=list(origins)), app_env="test"
    )


@pytest.fixture
async def admin_origin(db_session: AsyncSession) -> str:
    await _set_admin_origins(db_session, ADMIN_ORIGIN)
    return ADMIN_ORIGIN


def _preflight(
    origin: str = ADMIN_ORIGIN, method: str = "GET", req_headers: str | None = "authorization"
) -> dict[str, str]:
    headers = {"Origin": origin, "Access-Control-Request-Method": method}
    if req_headers is not None:
        headers["Access-Control-Request-Headers"] = req_headers
    return headers


def _no_cors(headers: Headers) -> bool:
    return not any(k.lower().startswith("access-control-") for k in headers)


def _admin_echo(resp: Response, origin: str = ADMIN_ORIGIN) -> None:
    h = resp.headers
    assert h["access-control-allow-origin"] == origin
    assert h["access-control-expose-headers"] == EXPOSE
    assert "access-control-allow-credentials" not in h
    assert h["cache-control"] == "no-store"
    assert "Origin" in h["vary"]


async def _token(db_session: AsyncSession, *perms: str) -> dict[str, str]:
    return bearer(mint(await make_staff(db_session, *perms)))


# --- preflight -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", APPS),
        ("PATCH", f"{APPS}/stoktok"),
        ("PUT", f"{APPS}/stoktok/roles/{uuid.uuid4()}/permissions"),
        ("DELETE", f"{ORGS}/{uuid.uuid4()}"),
    ],
)
async def test_preflight_admin_origin_allowed(
    client: AsyncClient, admin_origin: str, method: str, path: str
) -> None:
    resp = await client.options(
        path, headers=_preflight(method=method, req_headers="authorization,content-type")
    )
    assert resp.status_code == 204
    h = resp.headers
    assert h["access-control-allow-origin"] == admin_origin
    assert h["access-control-allow-methods"] == "DELETE, GET, PATCH, POST, PUT"
    assert h["access-control-allow-headers"] == "Authorization, Content-Type, If-Match"
    assert h["access-control-max-age"] == "600"
    assert h["vary"] == PREFLIGHT_VARY
    assert "access-control-allow-credentials" not in h


async def test_preflight_admin_origin_allowed_if_match(
    client: AsyncClient, admin_origin: str
) -> None:
    resp = await client.options(
        f"{ORGS}/{uuid.uuid4()}",
        headers=_preflight(method="PATCH", req_headers="authorization,content-type,if-match"),
    )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == admin_origin
    assert resp.headers["access-control-allow-headers"] == "Authorization, Content-Type, If-Match"


async def test_preflight_never_reaches_auth(client: AsyncClient, admin_origin: str) -> None:
    resp = await client.options(APPS, headers=_preflight(req_headers=None))
    assert resp.status_code == 204
    assert resp.content == b""
    assert "www-authenticate" not in resp.headers
    assert resp.headers["access-control-allow-origin"] == admin_origin


@pytest.mark.parametrize(
    "req_headers", ["x-mm-app", "cookie", "if-none-match", "authorization,x-mm-app"]
)
async def test_preflight_disallowed_method_or_header(
    client: AsyncClient, admin_origin: str, req_headers: str
) -> None:
    resp = await client.options(APPS, headers=_preflight(req_headers=req_headers))
    assert resp.status_code == 204
    assert not any(k.lower().startswith("access-control-allow-") for k in resp.headers)
    # A method the route does not serve is not an admin request: no CORS at all.
    resp = await client.options(APPS, headers=_preflight(method="PUT"))
    assert _no_cors(resp.headers)


@pytest.mark.parametrize(
    "headers",
    [
        [("Origin", "https://evil.mmlabs.online")],
        [("Origin", STOKTOK_ORIGIN)],
        [("Origin", "null")],
        [("Origin", ADMIN_ORIGIN), ("Origin", ADMIN_ORIGIN)],
        [("Origin", "https://ADMIN-PORTAL.mmlabs.online")],
    ],
)
async def test_unknown_origins_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_origin: str,
    headers: list[tuple[str, str]],
) -> None:
    await make_login_app(db_session)  # stoktok's origin is registered, on another app
    token = await _token(db_session, "admin:*")
    pre = await client.options(
        APPS,
        headers=Headers(
            [*headers, ("Access-Control-Request-Method", "GET")]  # type: ignore[list-item]
        ),
    )
    assert pre.status_code == 204
    assert _no_cors(pre.headers)
    actual = await client.get(APPS, headers=Headers([*headers, *token.items()]))
    assert actual.status_code == 200
    assert _no_cors(actual.headers)


# --- actual requests -------------------------------------------------------------------


async def test_export_exposes_truncation_headers(
    client: AsyncClient, db_session: AsyncSession, admin_origin: str
) -> None:
    resp = await client.get(
        f"{APPS.removesuffix('apps')}audit-logs/export",
        headers={"Origin": admin_origin, **await _token(db_session, "admin:audit:read")},
    )
    assert resp.status_code == 200, resp.text
    exposed = resp.headers["access-control-expose-headers"]
    assert "Content-Disposition, X-Export-Truncated, X-Export-Row-Count" in exposed


async def test_actual_get_with_bearer(
    client: AsyncClient, db_session: AsyncSession, admin_origin: str
) -> None:
    resp = await client.get(
        APPS, headers={"Origin": admin_origin, **await _token(db_session, "admin:*")}
    )
    assert resp.status_code == 200, resp.text
    _admin_echo(resp)


async def test_actual_patch_with_bearer(
    client: AsyncClient, db_session: AsyncSession, admin_origin: str
) -> None:
    headers = {"Origin": admin_origin, **await _token(db_session, "admin:*")}
    created = await client.post(ORGS, json={"slug": "acme", "name": "Acme"}, headers=headers)
    assert created.status_code == 201, created.text
    _admin_echo(created)
    resp = await client.patch(
        f"{ORGS}/{created.json()['id']}", json={"name": "Acme 2"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    _admin_echo(resp)


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("no_token", 401, "unauthorized"),
        ("bad_token", 401, "unauthorized"),
        ("forbidden", 403, None),
        ("not_found", 404, "not_found"),
        ("invalid", 422, "validation_error"),
    ],
)
async def test_error_envelopes_readable(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_origin: str,
    case: str,
    status: int,
    code: str | None,
) -> None:
    headers: dict[str, str] = {"Origin": admin_origin}
    if case == "bad_token":
        headers.update(bearer("not-a-jwt"))
    elif case == "forbidden":
        headers.update(await _token(db_session, "admin:users:read"))
    elif case != "no_token":
        headers.update(await _token(db_session, "admin:*"))
    if case == "invalid":
        resp = await client.post(ORGS, json={"slug": "Not Valid!"}, headers=headers)
    elif case == "not_found":
        resp = await client.get(f"{ORGS}/{uuid.uuid4()}", headers=headers)
    else:
        resp = await client.get(APPS, headers=headers)
    assert resp.status_code == status, resp.text
    assert "error" in resp.json()
    if code is not None:
        assert resp.json()["error"]["code"] == code
    if status == 401:
        assert resp.headers["www-authenticate"].startswith("Bearer ")
    _admin_echo(resp)


async def test_no_origin_only_vary_and_no_store(
    client: AsyncClient, db_session: AsyncSession, admin_origin: str
) -> None:
    token = await _token(db_session, "admin:*")
    with_origin = await client.get(APPS, headers={"Origin": admin_origin, **token})
    resp = await client.get(APPS, headers=token)
    assert resp.status_code == 200
    assert resp.json() == with_origin.json()
    assert _no_cors(resp.headers)
    assert resp.headers["vary"] == "Origin"
    assert resp.headers["cache-control"] == "no-store"
    unauth = await client.get(APPS)
    assert unauth.status_code == 401
    assert _no_cors(unauth.headers)
    assert unauth.headers["cache-control"] == "no-store"


async def _bearer_call(client: AsyncClient, method: str, path: str, headers: dict[str, str]) -> Any:
    body = {} if method in {"POST", "PATCH", "PUT"} else None
    return await client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize(("method", "path"), list(BEARER))
async def test_no_allow_credentials_on_any_bearer_route(
    client: AsyncClient, db_session: AsyncSession, admin_origin: str, method: str, path: str
) -> None:
    url = _fill(path)
    pre = await client.options(url, headers=_preflight(method=method))
    assert pre.headers["access-control-allow-origin"] == admin_origin
    assert "access-control-allow-credentials" not in pre.headers
    token = await _token(db_session, "admin:*")
    resp = await _bearer_call(client, method, url, {"Origin": admin_origin, **token})
    assert resp.headers["access-control-allow-origin"] == admin_origin
    assert "access-control-allow-credentials" not in resp.headers


async def test_cookie_only_is_401(
    client: AsyncClient, db_session: AsyncSession, admin_origin: str
) -> None:
    staff = await make_staff(db_session, "admin:*")
    raw, _, _ = await make_device(db_session, staff)
    resp = await client.get(
        APPS, headers=auth_headers(app_key=None, origin=ADMIN_ORIGIN, cookie=raw)
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    _admin_echo(resp)


# --- other scopes ----------------------------------------------------------------------


async def test_other_app_origin_refused_on_admin_but_ok_on_auth(
    client: AsyncClient, db_session: AsyncSession, admin_origin: str
) -> None:
    await make_login_app(db_session)
    resp = await client.get(
        APPS, headers={"Origin": STOKTOK_ORIGIN, **await _token(db_session, "admin:*")}
    )
    assert resp.status_code == 200
    assert _no_cors(resp.headers)
    auth = await client.post(TOKEN, json={}, headers={"Origin": STOKTOK_ORIGIN})
    assert auth.headers["access-control-allow-origin"] == STOKTOK_ORIGIN
    assert auth.headers["access-control-allow-credentials"] == "true"


async def test_admin_origin_still_credentialed_on_auth(
    client: AsyncClient, admin_origin: str
) -> None:
    resp = await client.options(
        TOKEN, headers=_preflight(method="POST", req_headers="content-type,x-mm-app")
    )
    assert resp.status_code == 204
    h = resp.headers
    assert h["access-control-allow-origin"] == admin_origin
    assert h["access-control-allow-credentials"] == "true"
    assert h["access-control-allow-methods"] == "GET, POST"
    assert h["access-control-allow-headers"] == "Content-Type, X-MM-App"


async def test_role_catalog_and_jwks_unchanged(
    client: AsyncClient, db_session: AsyncSession, admin_origin: str
) -> None:
    await make_login_app(db_session)
    catalog = await client.get(f"{APPS}/stoktok/roles", headers={"Origin": admin_origin})
    assert catalog.status_code == 200
    assert _no_cors(catalog.headers)
    assert "origin" not in catalog.headers.get("vary", "").lower()
    assert catalog.headers["etag"]
    assert catalog.headers["cache-control"] == "no-cache"
    jwks = await client.get("/.well-known/jwks.json", headers={"Origin": admin_origin})
    assert jwks.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in jwks.headers


async def test_roles_post_preflight_is_admin(client: AsyncClient, admin_origin: str) -> None:
    path = f"{APPS}/stoktok/roles"
    admin = await client.options(path, headers=_preflight(method="POST"))
    assert admin.headers["access-control-allow-origin"] == admin_origin
    assert admin.headers["access-control-allow-methods"] == "DELETE, GET, PATCH, POST, PUT"
    catalog = await client.options(path, headers=_preflight(method="GET"))
    assert _no_cors(catalog.headers)


# --- origin source ---------------------------------------------------------------------


async def _probe(client: AsyncClient, origin: str) -> Response:
    return await client.options(APPS, headers=_preflight(origin))


async def test_loopback_admin_origin_refused_in_production(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await db_session.execute(
        text("UPDATE apps SET origins = ARRAY[:a, :b] WHERE key = 'admin'"),
        {"a": ADMIN_ORIGIN, "b": LOCAL},
    )
    async with make_client() as client:  # test env: loopback allowed
        assert (await _probe(client, LOCAL)).headers["access-control-allow-origin"] == LOCAL
    async with make_client(**_PROD) as client:
        assert _no_cors((await _probe(client, LOCAL)).headers)
        resp = await _probe(client, ADMIN_ORIGIN)
        assert resp.headers["access-control-allow-origin"] == ADMIN_ORIGIN


async def test_disabled_or_empty_admin_origins_refused(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    async with make_client() as client:  # seeded admin row: no origins
        assert _no_cors((await _probe(client, ADMIN_ORIGIN)).headers)
    await db_session.execute(
        text("UPDATE apps SET origins = ARRAY[:a], status = 'disabled' WHERE key = 'admin'"),
        {"a": ADMIN_ORIGIN},
    )
    async with make_client() as client:
        assert _no_cors((await _probe(client, ADMIN_ORIGIN)).headers)


async def test_set_admin_origins_applies_in_process(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    await _set_admin_origins(db_session, ADMIN_ORIGIN)
    await db_session.commit()
    async with make_strict_client() as client:
        assert (await _probe(client, ADMIN_ORIGIN)).headers["access-control-allow-origin"]
        await _set_admin_origins(db_session, NEW_ORIGIN)
        await db_session.commit()  # invalidates the cache, well before the 60 s TTL
        assert (await _probe(client, NEW_ORIGIN)).headers["access-control-allow-origin"] == (
            NEW_ORIGIN
        )
        assert _no_cors((await _probe(client, ADMIN_ORIGIN)).headers)
        await _set_admin_origins(db_session)  # --clear
        await db_session.commit()
        assert _no_cors((await _probe(client, NEW_ORIGIN)).headers)
