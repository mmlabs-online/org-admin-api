import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.modules.auth.deps import get_signup_hook, get_turnstile_verifier
from app.modules.auth.passwords import PasswordHasher
from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.fakes import FakeTurnstileVerifier

URL = "/api/v1/auth/signup"
PASSWORD = "correct horse battery staple"


def _body(**overrides: Any) -> dict[str, Any]:
    return {"email": "bob@example.com", "password": PASSWORD, "turnstile_token": "pass"} | overrides


async def _users(db_session: AsyncSession) -> list[User]:
    return list((await db_session.scalars(select(User))).all())


async def test_signup_creates_unverified_active_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(URL, json=_body())
    assert resp.status_code == 201, resp.text
    (user,) = await _users(db_session)
    assert str(user.id) == resp.json()["id"]
    assert user.email_verified_at is None
    assert user.status == "active"
    assert await PasswordHasher(Settings()).verify(user.password_hash, PASSWORD)


async def test_signup_response_has_exact_keys(client: AsyncClient) -> None:
    resp = await client.post(URL, json=_body(email="Mixed.Case@Example.COM"))
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "email", "email_verified", "created_at"}
    assert body["email_verified"] is False
    assert body["email"] == "mixed.case@example.com"


async def test_duplicate_email_any_case_returns_409(client: AsyncClient) -> None:
    first = await client.post(URL, json=_body(email="Bob@Example.com"))
    assert first.status_code == 201
    resp = await client.post(URL, json=_body(email="bob@example.COM"))
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "email_taken"
    assert error["details"] is None
    assert "bob@example" not in resp.text.lower()
    assert first.json()["id"] not in resp.text


async def test_weak_password_too_short_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(URL, json=_body(password="Zq8!vR2#"))
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "weak_password"
    assert error["details"] == {"reasons": ["too_short"]}
    assert await _users(db_session) == []


async def test_common_password_returns_422(client: AsyncClient) -> None:
    resp = await client.post(URL, json=_body(password="Password1234"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "weak_password"
    assert resp.json()["error"]["details"] == {"reasons": ["too_common"]}


async def test_invalid_email_returns_422(client: AsyncClient) -> None:
    resp = await client.post(URL, json=_body(email="not-an-email"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_unknown_field_and_oversized_password_return_422(client: AsyncClient) -> None:
    resp = await client.post(URL, json=_body(is_admin=True))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"

    resp = await client.post(URL, json=_body(password="x" * 129))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"

    resp = await client.post(URL, json={"email": "bob@example.com", "password": PASSWORD})
    assert resp.status_code == 422


async def test_invalid_turnstile_returns_403_and_creates_no_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(URL, json=_body(turnstile_token="fail"))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "turnstile_failed"
    assert await _users(db_session) == []


async def test_signup_rate_limited_returns_429_with_retry_after(make_client: MakeClient) -> None:
    async with make_client(signup_rate_limit=2, signup_rate_window_seconds=60) as c:
        assert (await c.post(URL, json=_body(email="a@example.com"))).status_code == 201
        assert (await c.post(URL, json=_body(email="bad-email"))).status_code == 422
        resp = await c.post(URL, json=_body(email="bad-email"))
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    retry_after = int(resp.headers["Retry-After"])
    assert 1 <= retry_after <= 60
    assert resp.json()["error"]["details"] == {"retry_after": retry_after}


async def test_password_never_echoed_in_error_bodies(client: AsyncClient) -> None:
    secret = "Unique-Sentinel-Pa55phrase-7f3a"
    email = "sentinel.user@example.com"
    cases = [
        _body(email=email, password=secret + "x" * 128),  # 422 too long
        _body(email="not-an-email", password=secret),  # 422 bad email
        _body(email=email, password=secret, turnstile_token="fail"),  # 403
    ]
    for body in cases:
        resp = await client.post(URL, json=body)
        assert resp.status_code in {403, 422}
        assert secret not in resp.text
        assert email not in resp.text
    assert (await client.post(URL, json=_body(email=email, password=secret))).status_code == 201
    resp = await client.post(URL, json=_body(email=email, password=secret))
    assert resp.status_code == 409
    assert secret not in resp.text
    assert email not in resp.text


async def test_on_signup_hook_called_with_user_id(
    db_session: AsyncSession, fake_turnstile: FakeTurnstileVerifier
) -> None:
    calls: list[tuple[uuid.UUID, AsyncSession]] = []

    async def recorder(user_id: uuid.UUID, session: AsyncSession) -> None:
        calls.append((user_id, session))

    async def _session() -> Any:
        yield db_session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_turnstile_verifier] = lambda: fake_turnstile
    app.dependency_overrides[get_signup_hook] = lambda: recorder
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(URL, json=_body())
    assert resp.status_code == 201
    assert calls == [(uuid.UUID(resp.json()["id"]), db_session)]


async def test_turnstile_receives_client_ip(
    client: AsyncClient, fake_turnstile: FakeTurnstileVerifier
) -> None:
    resp = await client.post(
        URL, json=_body(), headers={"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2"}
    )
    assert resp.status_code == 201
    assert fake_turnstile.calls == [("pass", "127.0.0.1")]
