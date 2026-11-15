"""Request id middleware (ADM-004): keep a safe inbound `X-Request-ID`, else generate one."""

import uuid

import pytest
from httpx import AsyncClient


async def test_generated_when_absent(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert uuid.UUID(resp.headers["x-request-id"]).version == 4


async def test_valid_inbound_kept(client: AsyncClient) -> None:
    value = "Abc-123._" + "x" * 55  # 64 characters
    resp = await client.get("/healthz", headers={"X-Request-ID": value})
    assert resp.headers["x-request-id"] == value


@pytest.mark.parametrize("value", ["a" * 65, "has space", "<script>", "a\tb"])
async def test_invalid_inbound_replaced(client: AsyncClient, value: str) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": value})
    echoed = resp.headers["x-request-id"]
    assert echoed != value
    assert uuid.UUID(echoed).version == 4


async def test_echoed_on_error_response(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(
        f"/api/v1/organizations/{uuid.uuid4()}", headers={"X-Request-ID": "req-404"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert resp.headers["x-request-id"] == "req-404"


async def test_echoed_on_health(client: AsyncClient) -> None:
    for path in ("/healthz", "/readyz"):
        resp = await client.get(path, headers={"X-Request-ID": "req-health"})
        assert resp.status_code == 200
        assert resp.headers.get_list("x-request-id") == ["req-health"]
