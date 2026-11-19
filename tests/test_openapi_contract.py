import json
from typing import Any

import pytest
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
from app.modules.admin_auth.deps import _ERROR_RESPONSES, require_permission

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


def test_audit_export_operation() -> None:
    op = _schema()["paths"]["/api/v1/audit-logs/export"]["get"]
    ok = op["responses"]["200"]
    assert ok["content"] == {"text/csv": {"schema": {"type": "string"}}}
    assert {"Content-Disposition", "X-Export-Truncated", "X-Export-Row-Count"} <= set(ok["headers"])
    assert "X-Request-ID" in ok["headers"]
    assert op["x-permission"] == "admin:audit:read"
    for code in ("401", "403", "422"):
        assert op["responses"][code]["content"]["application/json"]["schema"] == {"$ref": REF}
    assert not {"limit", "offset"} & {p["name"] for p in op["parameters"]}


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


def test_apps_list_query_params() -> None:
    schema = _schema()
    op = schema["paths"]["/api/v1/apps"]["get"]
    names = ("status", "join_policy", "org_mode", "q", "sort")
    for name in ("limit", "offset", *names):
        param = _param(op, name)
        assert param["in"] == "query"
        assert not param.get("required")
    for name in names:
        assert _param(op, name)["description"]
    expected = {
        "status": ["active", "disabled"],
        "join_policy": ["open", "approval", "closed"],
        "org_mode": ["none", "required"],
    }
    for name, values in expected.items():
        enum = _flat(_param(op, name)["schema"])
        if "$ref" in enum:
            enum = schema["components"]["schemas"][enum["$ref"].rsplit("/", 1)[-1]]
        assert enum["enum"] == values
    q = _flat(_param(op, "q")["schema"])
    assert (q["minLength"], q["maxLength"]) == (1, 100)
    assert "422" in _param(op, "q")["description"]
    sort = _param(op, "sort")["schema"]
    assert sort["enum"] == ["key", "-key", "name", "-name", "created_at", "-created_at"]
    assert sort["default"] == "key"


def test_apps_list_422_is_envelope() -> None:
    response = _schema()["paths"]["/api/v1/apps"]["get"]["responses"]["422"]
    assert response["content"]["application/json"]["schema"] == {"$ref": REF}
    assert "validation_error" in response["description"]


_A, _C, _R = "/apps/{key}", "/apps/{key}/credentials", "/apps/{key}/roles/{role_id}"
_E, _U = "/organizations/{org_id}/entitlements", "/users/{user_id}"
_RA = "/users/{user_id}/role-assignments"
_JR = "/apps/{key}/join-requests/{request_id}"
# (method, path under /api/v1) -> declared subset of {404, 409}
_EXPECTED_ERRORS: dict[tuple[str, str], set[str]] = {
    ("get", "/apps"): set(),
    ("post", "/apps"): {"409"},
    ("get", _A): {"404"},
    ("patch", _A): {"404", "409"},
    ("delete", _A): {"404", "409"},
    ("get", f"{_A}/permissions"): {"404"},
    ("post", f"{_A}/permissions"): {"404", "409"},
    ("patch", f"{_A}/permissions/{{permission_id}}"): {"404", "409"},
    ("delete", f"{_A}/permissions/{{permission_id}}"): {"404", "409"},
    ("get", f"{_A}/role-definitions"): {"404"},
    ("get", f"{_A}/role-definitions/{{role_id}}"): {"404"},
    ("get", f"{_A}/role-definitions/{{role_id}}/assignments"): {"404"},
    ("post", f"{_A}/roles"): {"404", "409"},
    ("patch", _R): {"404", "409"},
    ("delete", _R): {"404", "409"},
    ("put", f"{_R}/permissions"): {"404", "409"},
    ("post", _C): {"404", "409"},
    ("get", _C): {"404"},
    ("delete", f"{_C}/{{credential_id}}"): {"404"},
    ("get", f"{_A}/join-requests"): {"404"},
    ("post", f"{_JR}/approve"): {"404", "409"},
    ("post", f"{_JR}/reject"): {"404", "409"},
    ("get", _E): {"404"},
    ("post", _E): {"404", "409"},
    ("get", f"{_E}/{{entitlement_id}}"): {"404"},
    ("patch", f"{_E}/{{entitlement_id}}"): {"404"},
    ("delete", f"{_E}/{{entitlement_id}}"): {"404"},
    ("get", f"{_U}/role-assignments"): {"404"},
    ("post", _RA): {"404", "409"},
    ("delete", f"{_RA}/{{assignment_id}}"): {"404", "409"},
    ("get", _U): {"404"},
    ("patch", _U): {"404", "409"},
}
_SUPER: dict[tuple[str, str], str] = {
    ("post", _C): "always",
    ("patch", _A): "conditional",
    ("post", f"{_A}/permissions"): "conditional",
    ("patch", f"{_A}/permissions/{{permission_id}}"): "conditional",
    ("delete", f"{_A}/permissions/{{permission_id}}"): "conditional",
    ("post", f"{_A}/roles"): "conditional",
    ("patch", _R): "conditional",
    ("delete", _R): "conditional",
    ("put", f"{_R}/permissions"): "conditional",
    ("post", _RA): "conditional",
    ("delete", f"{_RA}/{{assignment_id}}"): "conditional",
    ("patch", _U): "conditional",
}


def test_admin_declared_error_responses() -> None:
    paths = _schema()["paths"]
    for (method, path), expected in _EXPECTED_ERRORS.items():
        responses = paths[f"/api/v1{path}"][method]["responses"]
        assert {"404", "409"} & set(responses) == expected, (method, path)
        for code in expected:
            assert responses[code]["content"]["application/json"]["schema"] == {"$ref": REF}


def test_parameterized_admin_routes_declare_404() -> None:
    for path, method, op in _operations(_schema()):
        if "x-permission" in op and "{" in path:
            assert "404" in op["responses"], (method, path)


def test_requires_super_extension() -> None:
    for path, method, op in _operations(_schema()):
        key = (method, path.removeprefix("/api/v1"))
        value = op.get("x-requires-super")
        assert value == _SUPER.get(key), key
        when = op.get("x-requires-super-when")
        if value == "conditional":
            assert isinstance(when, str) and when, key
        else:
            assert when is None, key
        if value is not None:
            assert op["x-permission"] != "admin:*", key
            assert "admin_wildcard_required" in op["responses"]["403"]["description"], key


def test_post_apps_409_documents_codes() -> None:
    description = _schema()["paths"]["/api/v1/apps"]["post"]["responses"]["409"]["description"]
    assert "app_key_taken" in description
    assert "origin_taken" in description


def test_require_permission_rejects_bad_arguments() -> None:
    for kwargs in (
        {"errors": {401: "x"}},
        {"errors": {403: "x"}},
        {"errors": {500: "x"}},
        {"requires_super": "conditional"},
        {"super_when": "x"},
        {"requires_super": "always", "super_when": "x"},
    ):
        with pytest.raises(ValueError):
            require_permission("admin:apps:read", **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        require_permission("admin:*", requires_super="always")
    first = require_permission("admin:apps:read", errors={404: "x"})
    second = require_permission("admin:apps:read")
    assert first["responses"] is not second["responses"]
    first["responses"][401]["description"] = "changed"
    assert second["responses"][401] != first["responses"][401]
    assert _ERROR_RESPONSES[401]["description"] != "changed"
    assert 404 not in _ERROR_RESPONSES


_ORG_ITEM = "/api/v1/organizations/{org_id}"
_ENT_ITEM = "/api/v1/organizations/{org_id}/entitlements/{entitlement_id}"


def test_patch_412_and_if_match_declared() -> None:
    paths = _schema()["paths"]
    for path in (_ORG_ITEM, _ENT_ITEM):
        op = paths[path]["patch"]
        assert op["responses"]["412"]["content"]["application/json"]["schema"] == {"$ref": REF}
        param = _param(op, "If-Match")
        assert param["in"] == "header"
        assert not param.get("required")
        assert "ETag" in op["responses"]["200"]["headers"]


def test_etag_header_declared() -> None:
    paths = _schema()["paths"]
    for path, method, code in (
        (_ORG_ITEM, "get", "200"),
        (_ENT_ITEM, "get", "200"),
        ("/api/v1/organizations", "post", "201"),
        ("/api/v1/organizations/{org_id}/entitlements", "post", "201"),
    ):
        assert "ETag" in paths[path][method]["responses"][code]["headers"], (path, method)
