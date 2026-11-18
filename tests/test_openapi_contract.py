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
    assert "/api/v1/me/permissions" in checked
    assert "/api/v1/apps/{key}/role-definitions" in checked


def test_me_permissions_operation() -> None:
    schema = _schema()
    op = schema["paths"]["/api/v1/me/permissions"]["get"]
    ok = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok == {"$ref": "#/components/schemas/MePermissions"}
    assert set(op["responses"]) == {"200", "401"}
    assert op["responses"]["401"]["content"]["application/json"]["schema"] == {"$ref": REF}
    assert "x-permission" not in op
    component = schema["components"]["schemas"]["MePermissions"]
    assert set(component["properties"]) == {"user_id", "roles", "permissions"}
    assert set(component["required"]) == {"user_id", "roles", "permissions"}


def test_role_definitions_operations() -> None:
    schema = _schema()
    base = "/api/v1/apps/{key}/role-definitions"
    expected = {
        base: "#/components/schemas/Page_RoleDefinitionRead_",
        f"{base}/{{role_id}}": "#/components/schemas/RoleDefinitionRead",
    }
    for path, ref in expected.items():
        op = schema["paths"][path]["get"]
        assert op["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": ref}
        assert op["x-permission"] == "admin:apps:read"
        for code in ("401", "403"):
            assert op["responses"][code]["content"]["application/json"]["schema"] == {"$ref": REF}
    component = schema["components"]["schemas"]["RoleDefinitionRead"]
    assert set(component["required"]) == {
        "id", "app", "key", "name", "is_system", "is_default", "privileged",
        "permissions", "created_at", "updated_at",
    }  # fmt: skip


def test_role_holders_operation() -> None:
    schema = _schema()
    path = "/api/v1/apps/{key}/role-definitions/{role_id}/assignments"
    op = schema["paths"][path]["get"]
    ok = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok == {"$ref": "#/components/schemas/Page_RoleHolderRead_"}
    assert op["x-permission"] == "admin:users:read"
    for code in ("401", "403", "404"):
        assert op["responses"][code]["content"]["application/json"]["schema"] == {"$ref": REF}
    component = schema["components"]["schemas"]["RoleHolderRead"]
    assert set(component["required"]) == {
        "id", "app", "role_id", "role_key", "org_id", "granted_by", "expires_at", "active",
        "created_at", "user_id", "email", "display_name", "user_status",
    }  # fmt: skip


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


def _param(op: dict[str, Any], name: str) -> dict[str, Any]:
    return next(p for p in op["parameters"] if p["name"] == name)


def _flat(schema: dict[str, Any]) -> dict[str, Any]:
    """The non-null branch of an optional parameter schema."""
    if "anyOf" in schema:
        return next(s for s in schema["anyOf"] if s.get("type") != "null")
    return schema


def test_organizations_list_query_params() -> None:
    schema = _schema()
    op = schema["paths"]["/api/v1/organizations"]["get"]
    for name in ("limit", "offset", "status", "q", "sort"):
        param = _param(op, name)
        assert param["in"] == "query"
        assert not param.get("required")
    for name in ("status", "q", "sort"):
        assert _param(op, name)["description"]
    status = _flat(_param(op, "status")["schema"])
    if "$ref" in status:
        status = schema["components"]["schemas"][status["$ref"].rsplit("/", 1)[-1]]
    assert status["enum"] == ["active", "suspended"]
    q = _flat(_param(op, "q")["schema"])
    assert (q["minLength"], q["maxLength"]) == (1, 100)
    assert "422" in _param(op, "q")["description"]
    sort = _param(op, "sort")["schema"]
    assert sort["enum"] == ["created_at", "-created_at", "name", "-name", "slug", "-slug"]
    assert sort["default"] == "-created_at"


def test_organizations_declared_error_responses() -> None:
    paths = _schema()["paths"]
    item = paths["/api/v1/organizations/{org_id}"]
    for method in ("get", "patch", "delete"):
        assert item[method]["responses"]["404"]["content"]["application/json"]["schema"] == {
            "$ref": REF
        }
    assert "409" not in item["patch"]["responses"]
    for op in (item["delete"], paths["/api/v1/organizations"]["post"]):
        assert op["responses"]["409"]["content"]["application/json"]["schema"] == {"$ref": REF}


def test_organizations_list_422_is_envelope() -> None:
    response = _schema()["paths"]["/api/v1/organizations"]["get"]["responses"]["422"]
    assert response["content"]["application/json"]["schema"] == {"$ref": REF}
    assert "validation_error" in response["description"]
