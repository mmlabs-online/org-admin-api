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


def test_docs_enabled_in_production() -> None:
    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        app_env="production",
        argon2_memory_kib=65536,  # meet the production Argon2 floor
        argon2_time_cost=3,
    )
    app = create_app(settings)
    assert app.docs_url == "/docs"
    assert app.openapi_url == "/openapi.json"
