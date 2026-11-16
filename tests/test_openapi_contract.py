import json
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    ErrorEnvelope,
    RateLimitedError,
    register_error_handlers,
)
from app.main import create_app

REF = "#/components/schemas/ErrorEnvelope"


def _schema() -> dict[str, Any]:
    return create_app(Settings.model_construct(app_env="local")).openapi()


def _operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, op)
        for path, item in schema["paths"].items()
        for method, op in item.items()
        if method in {"get", "put", "post", "delete", "patch"}
    ]


def test_error_envelope_component() -> None:
    schemas = _schema()["components"]["schemas"]
    envelope, body = schemas["ErrorEnvelope"], schemas["ErrorBody"]
    assert envelope["required"] == ["error"]
    assert envelope["properties"]["error"] == {"$ref": "#/components/schemas/ErrorBody"}
    assert set(body["required"]) == {"code", "message", "details"}
    assert body["properties"]["code"]["type"] == "string"
    assert body["properties"]["message"]["type"] == "string"
    assert "type" not in body["properties"]["details"]


def test_every_error_response_references_envelope() -> None:
    checked = set()
    for path, method, op in _operations(_schema()):
        for code, response in op["responses"].items():
            if int(code) >= 400:
                assert response["content"]["application/json"]["schema"] == {"$ref": REF}, (
                    path,
                    method,
                    code,
                )
                checked.add(path)
    assert "/api/v1/users" in checked
    assert "/api/v1/auth/password/forgot" in checked
    assert "/api/v1/apps/{key}/roles" in checked


def test_no_5xx_documented() -> None:
    for _, _, op in _operations(_schema()):
        assert all(int(code) < 500 for code in op["responses"])


def test_no_fastapi_validation_schemas() -> None:
    schema = _schema()
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "ValidationError" not in schema["components"]["schemas"]
    text = json.dumps(schema)
    assert '/HTTPValidationError"' not in text
    assert '/ValidationError"' not in text


def test_422_is_envelope_validation_error() -> None:
    paths = _schema()["paths"]
    for path in ("/api/v1/organizations", "/api/v1/auth/password/forgot"):
        response = paths[path]["post"]["responses"]["422"]
        assert response["content"]["application/json"]["schema"] == {"$ref": REF}
        assert "validation_error" in response["description"]


def test_origin_header_not_declared() -> None:
    schema = _schema()
    for _, _, op in _operations(schema):
        for param in op.get("parameters", []):
            assert not (param["in"] == "header" and param["name"].lower() == "origin")
    for path in (
        "/api/v1/auth/password/forgot",
        "/api/v1/auth/verify-email/resend",
        "/api/v1/auth/signup",
    ):
        assert path in schema["paths"]


def test_x_mm_app_still_documented() -> None:
    op = _schema()["paths"]["/api/v1/auth/password/forgot"]["post"]
    assert any(p["in"] == "header" and p["name"] == "X-MM-App" for p in op["parameters"])


def test_x_request_id_documented_on_every_response() -> None:
    for _, _, op in _operations(_schema()):
        for response in op["responses"].values():
            assert response["headers"]["X-Request-ID"]["schema"] == {
                "type": "string",
                "format": "uuid",
            }


def test_schema_generation_does_not_mutate_shared_dicts() -> None:
    from app.modules.admin_auth.deps import _ERROR_RESPONSES
    from app.modules.auth.router import _ERR

    assert _schema() == _schema()
    for shared in (_ERR, *_ERROR_RESPONSES.values()):
        assert "content" not in shared
        assert "headers" not in shared


def test_existing_extensions_preserved() -> None:
    schema = _schema()
    assert any("x-permission" in op for _, _, op in _operations(schema))
    assert schema["components"]["securitySchemes"]


async def test_real_error_bodies_match_envelope(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    probe = FastAPI()
    register_error_handlers(probe)

    @probe.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("x")

    @probe.get("/limited")
    async def limited() -> None:
        raise RateLimitedError(retry_after=3)

    async with AsyncClient(transport=ASGITransport(app=probe), base_url="http://test") as p:
        responses = {
            401: await client.get("/api/v1/users"),
            403: await client.post("/api/v1/auth/password/forgot", json={}),
            404: await client.get("/nope"),
            409: await p.get("/conflict"),
            422: await admin_client.post("/api/v1/organizations", json={"slug": "Bad!"}),
            429: await p.get("/limited"),
        }
    for status, resp in responses.items():
        assert resp.status_code == status
        ErrorEnvelope.model_validate(resp.json())
        assert set(resp.json()["error"]) == {"code", "message", "details"}
