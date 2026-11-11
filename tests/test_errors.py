from http import HTTPStatus

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.errors import RateLimitedError, UnprocessableError, register_error_handlers


async def test_validation_error_details_have_no_input_or_ctx(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/organizations", json={"slug": "Not Valid!", "name": "x" * 300}
    )
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert details
    for detail in details:
        assert set(detail) == {"type", "loc", "msg"}
    assert "Not Valid!" not in resp.text


async def test_app_error_headers_are_passed_through() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/limited")
    async def limited() -> None:
        raise RateLimitedError(retry_after=7)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/limited")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "7"
    assert resp.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Too many requests",
            "details": {"retry_after": 7},
        }
    }


async def test_unprocessable_error_envelope() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/refused")
    async def refused() -> None:
        raise UnprocessableError("x", details=[{"field": "f", "reason": "r"}])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/refused")
    assert resp.status_code == 422
    assert resp.json() == {
        "error": {
            "code": "unprocessable",
            "message": "x",
            "details": [{"field": "f", "reason": "r"}],
        }
    }


# --- framework HTTPException envelope (ADM-011) -------------------------------


def _raising_app(exc: HTTPException) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    return app


async def test_unknown_path_404_envelope(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/nope")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "Not Found", "details": None}}


async def test_http_exception_detail_not_echoed_headers_kept() -> None:
    app = _raising_app(HTTPException(400, detail="secret-marker", headers={"X-Test": "1"}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/boom")
    assert resp.status_code == 400
    assert resp.json() == {
        "error": {"code": "bad_request", "message": "Bad Request", "details": None}
    }
    assert "secret-marker" not in resp.text
    assert resp.headers["X-Test"] == "1"


async def test_unmapped_status_uses_http_error() -> None:
    app = _raising_app(HTTPException(418))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/boom")
    assert resp.status_code == 418
    assert resp.json() == {
        "error": {"code": "http_error", "message": HTTPStatus(418).phrase, "details": None}
    }


async def test_304_http_exception_has_empty_body() -> None:
    app = _raising_app(HTTPException(304))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/boom")
    assert resp.status_code == 304
    assert resp.content == b""
