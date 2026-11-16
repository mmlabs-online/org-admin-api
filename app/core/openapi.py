"""OpenAPI document post-processing: the real error envelope and headers (ADM-013)."""

import copy
import json
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.core.errors import ErrorEnvelope

_ENVELOPE_REF = {"$ref": "#/components/schemas/ErrorEnvelope"}
_FASTAPI_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")
_REQUEST_ID_HEADER = {
    "description": "Server-generated request id (UUID4); quote it to support.",
    "schema": {"type": "string", "format": "uuid"},
}
_VALIDATION_DESCRIPTION = (
    "Validation failed (code=validation_error; also code=unprocessable for refused data)"
)
_GENERIC_DESCRIPTIONS = {"Validation Error", "Error envelope"}
_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _postprocess(schema: dict[str, Any]) -> None:
    envelope = ErrorEnvelope.model_json_schema(ref_template="#/components/schemas/{model}")
    schemas: dict[str, Any] = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(envelope.pop("$defs", {}))
    schemas["ErrorEnvelope"] = envelope
    for name in _FASTAPI_VALIDATION_SCHEMAS:
        schemas.pop(name, None)
    for item in schema.get("paths", {}).values():
        for method, operation in item.items():
            if method not in _METHODS:
                continue
            for code, response in operation.get("responses", {}).items():
                if code.isdigit() and int(code) >= 400:
                    response["content"] = {"application/json": {"schema": dict(_ENVELOPE_REF)}}
                    if code == "422" and response.get("description") in _GENERIC_DESCRIPTIONS:
                        response["description"] = _VALIDATION_DESCRIPTION
                response.setdefault("headers", {}).setdefault(
                    "X-Request-ID", copy.deepcopy(_REQUEST_ID_HEADER)
                )


def install_openapi(app: FastAPI) -> None:
    def custom() -> dict[str, Any]:
        if app.openapi_schema is None:
            # Deep copy: response entries can share module-level dicts across routes.
            schema = copy.deepcopy(
                get_openapi(
                    title=app.title,
                    version=app.version,
                    openapi_version=app.openapi_version,
                    summary=app.summary,
                    description=app.description,
                    routes=app.routes,
                    webhooks=app.webhooks.routes,
                    tags=app.openapi_tags,
                    servers=app.servers,
                    terms_of_service=app.terms_of_service,
                    contact=app.contact,
                    license_info=app.license_info,
                )
            )
            _postprocess(schema)
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom  # type: ignore[method-assign]


def render_openapi(app: FastAPI) -> bytes:
    document = json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False)
    return (document + "\n").encode("utf-8")
