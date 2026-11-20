"""Raw JSON body reading for machine routes (ADM-034, ADR-0021 §3).

No Pydantic body parameter: the route authenticates first (headers only), and only then reads a
size-capped body, so an unauthenticated caller never makes the server buffer or parse anything.
Order: `Content-Length` (411 / 413), content type (415), bounded read (413 / 422), validation.
"""

import asyncio
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.requests import ClientDisconnect

from app.core.errors import (
    LengthRequiredError,
    PayloadTooLargeError,
    UnprocessableError,
    UnsupportedMediaTypeError,
)


def _declared_length(request: Request, cap: int) -> int:
    value = request.headers.get("content-length")
    if value is None or not value.isascii() or not value.isdigit():
        raise LengthRequiredError("Content-Length is required")
    length = int(value)
    if length > cap:
        raise PayloadTooLargeError("Request body is too large")
    return length


def _is_json(request: Request) -> bool:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return media_type == "application/json"


def _incomplete() -> UnprocessableError:
    return UnprocessableError(
        "Request body is incomplete", details=[{"field": "body", "reason": "incomplete"}]
    )


async def _read(request: Request, length: int, cap: int) -> bytes:
    chunks: list[bytes] = []
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > length or received > cap:
                raise PayloadTooLargeError("Request body is too large")
            chunks.append(chunk)
    except ClientDisconnect:
        raise _incomplete() from None
    if received != length:
        raise _incomplete()
    return b"".join(chunks)


async def read_json_body[M: BaseModel](
    request: Request, model: type[M], *, cap: int, timeout_s: float
) -> M:
    length = _declared_length(request, cap)
    if not _is_json(request):
        raise UnsupportedMediaTypeError("Content-Type must be application/json")
    try:
        async with asyncio.timeout(timeout_s):
            raw = await _read(request, length, cap)
    except TimeoutError:
        raise _incomplete() from None
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        # The app's handler keeps only type, loc and msg; values are never echoed.
        raise RequestValidationError(
            [
                {**e, "loc": ("body", *e["loc"])}
                for e in exc.errors(include_url=False, include_input=False, include_context=False)
            ]
        ) from None


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema with `$defs` inlined, for a hand-written `requestBody`."""
    schema = model.model_json_schema()
    defs: dict[str, Any] = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return resolve(defs[ref.removeprefix("#/$defs/")])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved
