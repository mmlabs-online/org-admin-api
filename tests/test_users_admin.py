import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.main import create_app
from app.modules.users.models import User
from tests.conftest import MakeClient

BASE = "/api/v1/users"
USER_READ_KEYS = {
    "id",
    "email",
    "status",
    "email_verified_at",
    "display_name",
    "created_at",
    "updated_at",
}
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def users_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_users_api_enabled=True) as c:
        yield c


async def _user(
    db_session: AsyncSession,
    email: str,
    *,
    status: str = "active",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> User:
    user = User(email=email, password_hash="h", status=status)
    if created_at is not None:
        user.created_at = created_at
    if updated_at is not None:
        user.updated_at = updated_at
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


def _emails(body: dict[str, Any]) -> list[str]:
    return [item["email"] for item in body["items"]]


# --- exposure (AC 5) ---------------------------------------------------------


async def test_users_api_not_mounted_by_default(client: AsyncClient) -> None:
    some_id = uuid.uuid4()
    assert (await client.get(BASE)).status_code == 404
    assert (await client.get(f"{BASE}/{some_id}")).status_code == 404
    resp = await client.patch(f"{BASE}/{some_id}", json={"status": "disabled"})
    assert resp.status_code == 404


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_users_api_flag_refused_in_staging_and_production(app_env: str) -> None:
    with pytest.raises(ValidationError, match="ADMIN_USERS_API_ENABLED"):
        Settings(
            app_env=app_env,  # type: ignore[arg-type]
            admin_users_api_enabled=True,
            argon2_memory_kib=65536,  # meet the Argon2 floor so only the flag fails
            argon2_time_cost=3,
        )


@pytest.mark.parametrize("app_env", ["local", "test"])
def test_users_api_flag_allowed_in_local_and_test(app_env: str) -> None:
    settings = Settings(app_env=app_env, admin_users_api_enabled=True)  # type: ignore[arg-type]
    assert settings.admin_users_api_enabled is True


# --- list (AC 1) -------------------------------------------------------------


async def test_list_orders_by_created_at_desc_then_id(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    oldest = await _user(db_session, "oldest@x.io", created_at=T0)
    middle = await _user(db_session, "middle@x.io", created_at=T0 + timedelta(days=1))
    newest = await _user(db_session, "newest@x.io", created_at=T0 + timedelta(days=3))
    tie_a = await _user(db_session, "tie-a@x.io", created_at=T0 + timedelta(days=2))
    tie_b = await _user(db_session, "tie-b@x.io", created_at=T0 + timedelta(days=2))
    ties = sorted([tie_a, tie_b], key=lambda u: u.id)

    resp = await users_client.get(BASE)
    assert resp.status_code == 200
    expected = [newest, *ties, middle, oldest]
    assert [item["id"] for item in resp.json()["items"]] == [str(u.id) for u in expected]


async def test_list_paginates_and_total_respects_filters(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _user(db_session, "a@x.io", created_at=T0 + timedelta(days=3))
    await _user(db_session, "b@x.io", created_at=T0 + timedelta(days=2), status="disabled")
    await _user(db_session, "c@x.io", created_at=T0 + timedelta(days=1))

    first = (await users_client.get(BASE, params={"limit": 2, "offset": 0})).json()
    assert _emails(first) == ["a@x.io", "b@x.io"]
    assert (first["total"], first["limit"], first["offset"]) == (3, 2, 0)

    second = (await users_client.get(BASE, params={"limit": 2, "offset": 2})).json()
    assert _emails(second) == ["c@x.io"]
    assert (second["total"], second["limit"], second["offset"]) == (3, 2, 2)

    filtered = (await users_client.get(BASE, params={"status": "disabled", "limit": 1})).json()
    assert _emails(filtered) == ["b@x.io"]
    assert filtered["total"] == 1


async def test_list_email_filter_is_case_insensitive_contains(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _user(db_session, "alice@example.com", created_at=T0 + timedelta(days=1))
    await _user(db_session, "bob@other.io", created_at=T0)

    resp = await users_client.get(BASE, params={"email": "EXAMPLE"})
    assert _emails(resp.json()) == ["alice@example.com"]

    resp = await users_client.get(BASE, params={"email": "o"})
    assert _emails(resp.json()) == ["alice@example.com", "bob@other.io"]
    assert resp.json()["total"] == 2


async def test_list_email_filter_escapes_like_wildcards(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _user(db_session, "a%b@x.io")
    await _user(db_session, "a_b@x.io")
    await _user(db_session, "a\\b@x.io")
    await _user(db_session, "axb@x.io")

    for term, expected in [
        ("%", ["a%b@x.io"]),
        ("_", ["a_b@x.io"]),
        ("\\", ["a\\b@x.io"]),
        ("a_b", ["a_b@x.io"]),
    ]:
        resp = await users_client.get(BASE, params={"email": term})
        assert resp.status_code == 200
        assert _emails(resp.json()) == expected, term


async def test_list_status_filter(users_client: AsyncClient, db_session: AsyncSession) -> None:
    await _user(db_session, "on@x.io")
    await _user(db_session, "off@x.io", status="disabled")

    disabled = (await users_client.get(BASE, params={"status": "disabled"})).json()
    assert _emails(disabled) == ["off@x.io"]
    active = (await users_client.get(BASE, params={"status": "active"})).json()
    assert _emails(active) == ["on@x.io"]


@pytest.mark.parametrize(
    "params",
    [
        {"status": "banned"},
        {"email": ""},
        {"email": "a" * 255},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
async def test_list_rejects_bad_query(users_client: AsyncClient, params: dict[str, Any]) -> None:
    resp = await users_client.get(BASE, params=params)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- get (AC 2) --------------------------------------------------------------


async def test_get_user(users_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _user(db_session, "get@x.io")
    resp = await users_client.get(f"{BASE}/{user.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(user.id)
    assert body["email"] == "get@x.io"
    assert body["status"] == "active"
    assert body["email_verified_at"] is None
    assert body["display_name"] is None


async def test_get_missing_user_returns_404(users_client: AsyncClient) -> None:
    missing = uuid.uuid4()
    resp = await users_client.get(f"{BASE}/{missing}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert str(missing) not in resp.text


async def test_get_bad_uuid_returns_422(users_client: AsyncClient) -> None:
    resp = await users_client.get(f"{BASE}/not-a-uuid")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- patch (AC 2) ------------------------------------------------------------


async def test_patch_disables_and_reenables(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "toggle@x.io")

    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "disabled"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"
    await db_session.refresh(user)
    assert user.status == "disabled"

    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "active"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    await db_session.refresh(user)
    assert user.status == "active"


async def test_patch_same_status_is_noop(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    past = datetime(2020, 1, 1, tzinfo=UTC)
    user = await _user(db_session, "noop@x.io", updated_at=past)

    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "active"})
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["updated_at"]) == past

    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "disabled"})
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["updated_at"]) != past


async def test_patch_missing_user_returns_404(users_client: AsyncClient) -> None:
    resp = await users_client.patch(f"{BASE}/{uuid.uuid4()}", json={"status": "disabled"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": None},
        {"status": "banned"},
        {"status": "disabled", "email": "x@y.z"},
        {"status": "disabled", "password_hash": "x"},
    ],
)
async def test_patch_rejects_invalid_bodies(
    users_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    user = await _user(db_session, "strict@x.io")
    resp = await users_client.patch(f"{BASE}/{user.id}", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    await db_session.refresh(user)
    assert (user.status, user.email, user.password_hash) == ("active", "strict@x.io", "h")


# --- response shape (AC 3) ---------------------------------------------------


async def test_user_read_has_exact_keys(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = User(email="keys@x.io", password_hash="$argon2id$secret-hash-value")
    db_session.add(user)
    await db_session.flush()

    responses = [
        await users_client.get(BASE),
        await users_client.get(f"{BASE}/{user.id}"),
        await users_client.patch(f"{BASE}/{user.id}", json={"status": "disabled"}),
    ]
    bodies = [responses[0].json()["items"][0], responses[1].json(), responses[2].json()]
    for resp, body in zip(responses, bodies, strict=True):
        assert resp.status_code == 200
        assert set(body) == USER_READ_KEYS
        assert "password_hash" not in resp.text
        assert "secret-hash-value" not in resp.text


# --- permissions (AC 4) ------------------------------------------------------


def test_users_routes_declare_permissions() -> None:
    # FastAPI wraps included routers lazily, so enumerate operations via the OpenAPI schema.
    app = create_app(Settings(admin_users_api_enabled=True))
    expected = {
        ("GET", "/api/v1/users"): "admin:users:read",
        ("GET", "/api/v1/users/{user_id}"): "admin:users:read",
        ("PATCH", "/api/v1/users/{user_id}"): "admin:users:write",
    }
    found = {
        (method.upper(), path): operation.get("x-permission")
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/users")
        for method, operation in operations.items()
    }
    assert found == expected
