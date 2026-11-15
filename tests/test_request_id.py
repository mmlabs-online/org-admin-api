"""Request id middleware (ADM-004, ADM-004b): keep a safe inbound `X-Request-ID` only from a
trusted proxy peer, else generate one."""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from tests.conftest import MakeClient

TRUSTED = ["127.0.0.0/8"]  # the ASGI test client's peer is 127.0.0.1


@pytest.fixture
async def trusted_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(trusted_proxy_cidrs=TRUSTED) as c:
        yield c


async def test_generated_when_absent(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert uuid.UUID(resp.headers["x-request-id"]).version == 4


async def test_valid_inbound_kept(trusted_client: AsyncClient) -> None:
    value = "Abc-123._" + "x" * 55  # 64 characters
    resp = await trusted_client.get("/healthz", headers={"X-Request-ID": value})
    assert resp.headers["x-request-id"] == value


@pytest.mark.parametrize("value", ["a" * 65, "has space", "<script>", "a\tb"])
async def test_invalid_inbound_replaced(trusted_client: AsyncClient, value: str) -> None:
    resp = await trusted_client.get("/healthz", headers={"X-Request-ID": value})
    echoed = resp.headers["x-request-id"]
    assert echoed != value
    assert uuid.UUID(echoed).version == 4


async def test_inbound_ignored_from_untrusted_peer(make_client: MakeClient) -> None:
    async with make_client(trusted_proxy_cidrs=["10.0.0.0/8"]) as client:
        resp = await client.get("/healthz", headers={"X-Request-ID": "req-spoofed"})
    echoed = resp.headers["x-request-id"]
    assert echoed != "req-spoofed"
    assert uuid.UUID(echoed).version == 4


async def test_inbound_kept_from_trusted_peer(trusted_client: AsyncClient) -> None:
    resp = await trusted_client.get("/healthz", headers={"X-Request-ID": "req-proxied"})
    assert resp.headers["x-request-id"] == "req-proxied"


async def test_echoed_on_error_response(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=["admin:*"], trusted_proxy_cidrs=TRUSTED) as client:
        resp = await client.get(
            f"/api/v1/organizations/{uuid.uuid4()}", headers={"X-Request-ID": "req-404"}
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert resp.headers["x-request-id"] == "req-404"


async def test_echoed_on_health(trusted_client: AsyncClient) -> None:
    for path in ("/healthz", "/readyz"):
        resp = await trusted_client.get(path, headers={"X-Request-ID": "req-health"})
        assert resp.status_code == 200
        assert resp.headers.get_list("x-request-id") == ["req-health"]
