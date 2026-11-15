import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.email_verification import VERIFY, email_token_hash
from app.modules.auth.models import EmailToken
from app.modules.auth.password_reset import RESET
from app.modules.auth.repository import EmailTokenRepository
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
    async with make_client(admin_permissions=["admin:*"]) as c:
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


async def _issue(db_session: AsyncSession, user: User, purpose: str) -> str:
    raw = f"{purpose}-{uuid.uuid4().hex}"
    await EmailTokenRepository(db_session).issue(
        user.id, purpose, email_token_hash(raw), timedelta(minutes=30)
    )
    return raw


async def _unused_tokens(db_session: AsyncSession, user: User) -> int:
    count = await db_session.scalar(
        select(func.count())
        .select_from(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.used_at.is_(None))
    )
    return count or 0


async def test_disable_invalidates_email_tokens(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "tokens@x.io")
    reset = await _issue(db_session, user, RESET)
    verify = await _issue(db_session, user, VERIFY)
    assert await _unused_tokens(db_session, user) == 2

    for status in ("disabled", "active"):
        resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": status})
        assert resp.status_code == 200, resp.text
    assert await _unused_tokens(db_session, user) == 0

    resp = await users_client.post(
        "/api/v1/auth/password/reset",
        json={"token": reset, "new_password": "a brand new long passphrase"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "token_invalid"
    resp = await users_client.post("/api/v1/auth/verify-email", json={"token": verify})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "token_invalid"


async def test_disable_leaves_other_users_tokens(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user(db_session, "target@x.io")
    other = await _user(db_session, "bystander@x.io")
    await _issue(db_session, user, RESET)
    await _issue(db_session, other, RESET)
    resp = await users_client.patch(f"{BASE}/{user.id}", json={"status": "disabled"})
    assert resp.status_code == 200
    assert await _unused_tokens(db_session, user) == 0
    assert await _unused_tokens(db_session, other) == 1


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
