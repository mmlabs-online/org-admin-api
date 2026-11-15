"""Request id middleware (ADM-004, ADM-002d): always a new UUID4, stored and echoed; an inbound
`X-Request-ID` is ignored from any peer."""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from tests.conftest import MakeClient

TRUSTED = ["127.0.0.0/8"]  # the ASGI test client's peer is 127.0.0.1


def _uuid4(value: str) -> bool:
    return uuid.UUID(value).version == 4


@pytest.fixture
async def trusted_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(trusted_proxy_cidrs=TRUSTED) as c:
        yield c


async def test_generated_when_absent(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert _uuid4(resp.headers["x-request-id"])


@pytest.mark.parametrize("peer", ["trusted", "untrusted"])
async def test_inbound_ignored(make_client: MakeClient, peer: str) -> None:
    cidrs = TRUSTED if peer == "trusted" else ["10.0.0.0/8"]
    async with make_client(trusted_proxy_cidrs=cidrs) as client:
        resp = await client.get("/healthz", headers={"X-Request-ID": "req-proxied"})
    echoed = resp.headers["x-request-id"]
    assert echoed != "req-proxied"
    assert _uuid4(echoed)


async def test_same_inbound_gives_distinct_ids(trusted_client: AsyncClient) -> None:
    ids = [
        (await trusted_client.get("/healthz", headers={"X-Request-ID": "req-same"})).headers[
            "x-request-id"
        ]
        for _ in range(2)
    ]
    assert ids[0] != ids[1]
    assert all(_uuid4(i) for i in ids)


async def test_echoed_on_error_response(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=["admin:*"], trusted_proxy_cidrs=TRUSTED) as client:
        resp = await client.get(
            f"/api/v1/organizations/{uuid.uuid4()}", headers={"X-Request-ID": "req-404"}
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    echoed = resp.headers["x-request-id"]
    assert echoed != "req-404"
    assert _uuid4(echoed)


async def test_echoed_on_health(trusted_client: AsyncClient) -> None:
    for path in ("/healthz", "/readyz"):
        resp = await trusted_client.get(path, headers={"X-Request-ID": "req-health"})
        assert resp.status_code == 200
        values = resp.headers.get_list("x-request-id")
        assert len(values) == 1
        assert values[0] != "req-health"
        assert _uuid4(values[0])
