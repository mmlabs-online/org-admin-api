"""DynamicCORSMiddleware with a fake registry and a tiny inner ASGI app (USR-03e, ADR-0006)."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient, Headers
from starlette.types import Message, Receive, Scope, Send

from app.core.cors import DynamicCORSMiddleware

ORIGIN = "https://ricotdin.mmlabs.online"
EVIL = "https://evil.mmlabs.online"
PREFIX = "/api/v1/auth/"
PREFLIGHT_VARY = "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"


class FakeRegistry:
    def __init__(self, *origins: str) -> None:
        self.origins = set(origins)
        self.asked: list[str] = []

    async def is_allowed(self, origin: str) -> bool:
        self.asked.append(origin)
        return origin in self.origins


class Inner:
    """Records calls; responds 200 with configurable extra headers."""

    def __init__(self, headers: list[tuple[bytes, bytes]] | None = None) -> None:
        self.calls: list[Scope] = []
        self.headers = headers or []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls.append(scope)
        if scope["type"] != "http":
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain"), *self.headers],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture
def registry() -> FakeRegistry:
    return FakeRegistry(ORIGIN)


@pytest.fixture
def inner() -> Inner:
    return Inner()


@pytest.fixture
async def client(registry: FakeRegistry, inner: Inner) -> AsyncIterator[AsyncClient]:
    app = DynamicCORSMiddleware(inner, registry=registry, path_prefix=PREFIX)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _preflight(origin: str = ORIGIN, method: str = "POST", **extra: str) -> dict[str, str]:
    return {"Origin": origin, "Access-Control-Request-Method": method, **extra}


def _no_allow_headers(headers: Headers) -> bool:
    return not any(k.lower().startswith("access-control-allow-") for k in headers)


async def test_preflight_allowed(client: AsyncClient) -> None:
    resp = await client.options(
        f"{PREFIX}token",
        headers=_preflight(**{"Access-Control-Request-Headers": "content-type,x-mm-app"}),
    )
    assert resp.status_code == 204
    assert resp.content == b""
    h = resp.headers
    assert h["access-control-allow-origin"] == ORIGIN
    assert h["access-control-allow-credentials"] == "true"
    assert h["access-control-allow-methods"] == "GET, POST"
    assert h["access-control-allow-headers"] == "Content-Type, X-MM-App"
    assert h["access-control-max-age"] == "600"
    assert h["vary"] == PREFLIGHT_VARY


async def test_preflight_unknown_origin(client: AsyncClient) -> None:
    resp = await client.options(f"{PREFIX}token", headers=_preflight(EVIL))
    assert resp.status_code == 204
    assert _no_allow_headers(resp.headers)
    assert resp.headers["vary"] == PREFLIGHT_VARY


@pytest.mark.parametrize(
    ("method", "req_headers"),
    [
        ("PUT", None),
        ("DELETE", None),
        ("POST", "authorization"),
        ("POST", "x-mm-app, x-other"),
    ],
)
async def test_preflight_bad_method_or_header(
    client: AsyncClient, method: str, req_headers: str | None
) -> None:
    extra = {"Access-Control-Request-Headers": req_headers} if req_headers else {}
    resp = await client.options(f"{PREFIX}token", headers=_preflight(method=method, **extra))
    assert resp.status_code == 204
    assert _no_allow_headers(resp.headers)


@pytest.mark.parametrize("req_headers", ["X-MM-App, content-type", " Content-Type ", None])
async def test_preflight_allowed_header_variants(
    client: AsyncClient, req_headers: str | None
) -> None:
    extra = {"Access-Control-Request-Headers": req_headers} if req_headers else {}
    resp = await client.options(f"{PREFIX}login", headers=_preflight(method="GET", **extra))
    assert resp.headers["access-control-allow-origin"] == ORIGIN


async def test_preflight_never_reaches_app(client: AsyncClient, inner: Inner) -> None:
    await client.options(f"{PREFIX}token", headers=_preflight())
    await client.options(f"{PREFIX}token", headers=_preflight(EVIL))
    await client.options(f"{PREFIX}token", headers=_preflight(method="PUT"))
    assert inner.calls == []
    resp = await client.options(f"{PREFIX}token", headers={"Origin": ORIGIN})
    assert len(inner.calls) == 1  # no Access-Control-Request-Method: not a preflight
    assert resp.status_code == 200


async def test_actual_request_allowed_origin_echoed(client: AsyncClient) -> None:
    resp = await client.post(f"{PREFIX}login", headers={"Origin": ORIGIN})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert resp.headers["access-control-expose-headers"] == "Retry-After"


async def test_actual_request_unknown_origin_no_acao(client: AsyncClient) -> None:
    resp = await client.post(f"{PREFIX}login", headers={"Origin": EVIL})
    assert resp.status_code == 200
    assert resp.content == b"ok"
    assert resp.headers["content-type"] == "text/plain"
    for name in (
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-expose-headers",
    ):
        assert name not in resp.headers
    assert resp.headers["vary"] == "Origin"


async def test_null_and_missing_origin_no_acao(
    registry: FakeRegistry, inner: Inner, client: AsyncClient
) -> None:
    for headers in ({"Origin": "null"}, {}):
        resp = await client.post(f"{PREFIX}login", headers=headers)
        assert "access-control-allow-origin" not in resp.headers
        assert resp.headers["vary"] == "Origin"
    assert registry.asked == []
    app = DynamicCORSMiddleware(inner, registry=registry, path_prefix=PREFIX)
    responses: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        responses.append(message)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": f"{PREFIX}login",
        "headers": [(b"origin", ORIGIN.encode()), (b"origin", ORIGIN.encode())],
        "query_string": b"",
    }
    await app(scope, receive, send)
    names = {k.lower() for k, _ in responses[0]["headers"]}
    assert b"access-control-allow-origin" not in names
    assert registry.asked == []


@pytest.mark.parametrize(
    "origin", ["https://RICOTDIN.mmlabs.online", "https://ricotdin.mmlabs.online:443"]
)
async def test_case_variant_origin_not_echoed(client: AsyncClient, origin: str) -> None:
    resp = await client.post(f"{PREFIX}login", headers={"Origin": origin})
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.parametrize(
    ("inner_vary", "expected"),
    [
        ("Accept-Encoding", "Accept-Encoding, Origin"),
        ("Origin", "Origin"),
        ("accept-encoding, origin", "accept-encoding, origin"),
    ],
)
async def test_vary_origin_always_and_merged(
    registry: FakeRegistry, inner_vary: str, expected: str
) -> None:
    inner = Inner([(b"vary", inner_vary.encode())])
    app = DynamicCORSMiddleware(inner, registry=registry, path_prefix=PREFIX)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(f"{PREFIX}x", headers={"Origin": ORIGIN})
    assert resp.headers["vary"] == expected


async def test_no_store_added_when_absent(client: AsyncClient) -> None:
    resp = await client.post(f"{PREFIX}signup", headers={"Origin": ORIGIN})
    assert resp.headers["cache-control"] == "no-store"
    resp = await client.post(f"{PREFIX}signup")
    assert resp.headers["cache-control"] == "no-store"


async def test_existing_cache_control_kept(registry: FakeRegistry) -> None:
    inner = Inner([(b"cache-control", b"private, max-age=5")])
    app = DynamicCORSMiddleware(inner, registry=registry, path_prefix=PREFIX)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(f"{PREFIX}x", headers={"Origin": ORIGIN})
    assert resp.headers.get_list("cache-control") == ["private, max-age=5"]


async def _untouched(client: AsyncClient, inner: Inner, path: str) -> None:
    plain = await client.get(path)
    resp = await client.get(path, headers={"Origin": ORIGIN})
    assert resp.headers.raw == plain.headers.raw
    assert "vary" not in resp.headers
    assert "access-control-allow-origin" not in resp.headers
    before = len(inner.calls)
    pre = await client.options(path, headers=_preflight())
    assert len(inner.calls) == before + 1  # routed to the inner app
    assert "access-control-allow-origin" not in pre.headers


@pytest.mark.parametrize(
    "path",
    ["/api/v1/apps/x/roles", "/api/v1/organizations", "/healthz", "/.well-known/jwks.json"],
)
async def test_out_of_scope_paths_untouched(client: AsyncClient, inner: Inner, path: str) -> None:
    await _untouched(client, inner, path)


@pytest.mark.parametrize("path", ["/api/v1/authx/login", "/api/v1/auth"])
async def test_prefix_lookalike_untouched(client: AsyncClient, inner: Inner, path: str) -> None:
    await _untouched(client, inner, path)


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_non_http_scope_passthrough(
    registry: FakeRegistry, inner: Inner, scope_type: str
) -> None:
    app = DynamicCORSMiddleware(inner, registry=registry, path_prefix=PREFIX)
    scope: Scope = {"type": scope_type, "path": f"{PREFIX}x", "headers": []}

    async def receive() -> Message:
        raise AssertionError("not called")

    async def send(message: Message) -> None:
        raise AssertionError("not called")

    await app(scope, receive, send)
    assert inner.calls == [scope]
    assert registry.asked == []
