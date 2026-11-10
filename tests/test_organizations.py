import uuid

from httpx import AsyncClient

BASE = "/api/v1/organizations"


async def _create(client: AsyncClient, slug: str = "acme", name: str = "Acme") -> dict:
    resp = await client.post(BASE, json={"slug": slug, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_and_get(client: AsyncClient) -> None:
    org = await _create(client)
    assert org["status"] == "active"

    resp = await client.get(f"{BASE}/{org['id']}")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "acme"


async def test_duplicate_slug_conflicts(client: AsyncClient) -> None:
    await _create(client)
    resp = await client.post(BASE, json={"slug": "acme", "name": "Other"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_invalid_slug_is_rejected(client: AsyncClient) -> None:
    resp = await client.post(BASE, json={"slug": "Not Valid!", "name": "x"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_get_missing_returns_404(client: AsyncClient) -> None:
    resp = await client.get(f"{BASE}/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_list_paginates(client: AsyncClient) -> None:
    for i in range(3):
        await _create(client, slug=f"org-{i}", name=f"Org {i}")

    resp = await client.get(BASE, params={"limit": 2, "offset": 0})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 3
    assert len(body["items"]) == 2

    resp = await client.get(BASE, params={"limit": 2, "offset": 2})
    assert len(resp.json()["items"]) == 1


async def test_update_status_and_delete(client: AsyncClient) -> None:
    org = await _create(client)

    resp = await client.patch(f"{BASE}/{org['id']}", json={"status": "suspended"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"

    resp = await client.delete(f"{BASE}/{org['id']}")
    assert resp.status_code == 204
    assert (await client.get(f"{BASE}/{org['id']}")).status_code == 404
