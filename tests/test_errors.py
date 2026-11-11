from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import RateLimitedError, register_error_handlers


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
