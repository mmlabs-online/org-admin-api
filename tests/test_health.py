import pytest
from httpx import AsyncClient

from app.core.config import get_settings


async def test_liveness(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "dev"}


async def test_readiness_checks_database(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["database"] == "ok"


async def test_liveness_reports_configured_version(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "app_version", "v1.2.3")
    resp = await client.get("/healthz")
    assert resp.json()["version"] == "v1.2.3"
