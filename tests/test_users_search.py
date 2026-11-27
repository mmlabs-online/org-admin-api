"""USR-28: exact email match on `GET /users` and `POST /users/search` (D43)."""

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_app, make_role

BASE = "/api/v1/users"
SEARCH = f"{BASE}/search"


@pytest.fixture
async def users_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"]) as c:
        yield c


async def _user(db: AsyncSession, email: str, *, status: str = "active") -> User:
    user = User(email=email, password_hash="h", status=status)
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def _pair(db: AsyncSession) -> tuple[str, User, User, str]:
    """`an@<tag>` and `joan@<tag>` (the second contains the first as a substring)."""
    tag = f"{uuid.uuid4().hex[:8]}.test"
    an = await _user(db, f"an@{tag}")
    joan = await _user(db, f"joan@{tag}")
    return tag, an, joan, f"an@{tag}"


def _emails(resp: Response) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [i["email"] for i in resp.json()["items"]]


async def _get(c: AsyncClient, **params: Any) -> Response:
    return await c.get(BASE, params=params)


async def _post(c: AsyncClient, body: dict[str, Any]) -> Response:
    return await c.post(SEARCH, json=body)


def _unprocessable(resp: Response, field: str, reason: str) -> None:
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "unprocessable"
    assert error["details"][0] == {"field": field, "reason": reason}


async def test_get_exact_hit(users_client: AsyncClient, db_session: AsyncSession) -> None:
    _, an, _, email = await _pair(db_session)
    resp = await _get(users_client, email=email, email_match="exact")
    assert _emails(resp) == [an.email]
    assert resp.json()["total"] == 1


async def test_get_exact_case_insensitive_and_trimmed(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, an, _, email = await _pair(db_session)
    resp = await _get(users_client, email=f" {email.upper()} ", email_match="exact")
    assert _emails(resp) == [an.email]


async def test_get_exact_no_partial_hit(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    tag, an, _, email = await _pair(db_session)
    assert _emails(await _get(users_client, email=email, email_match="exact")) == [an.email]
    resp = await _get(users_client, email=f"n@{tag}", email_match="exact")
    assert _emails(resp) == []
    assert resp.json()["total"] == 0


async def test_get_default_is_contains(users_client: AsyncClient, db_session: AsyncSession) -> None:
    _, an, joan, email = await _pair(db_session)
    assert set(_emails(await _get(users_client, email=email))) == {an.email, joan.email}


async def test_get_explicit_contains_matches_default(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, an, joan, email = await _pair(db_session)
    resp = await _get(users_client, email=email, email_match="contains")
    assert set(_emails(resp)) == {an.email, joan.email}


async def test_get_bad_email_match_422(users_client: AsyncClient) -> None:
    resp = await _get(users_client, email="a@b.test", email_match="fuzzy")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert resp.json()["error"]["details"][0]["loc"] == ["query", "email_match"]


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_email_match_without_email_422(users_client: AsyncClient, method: str) -> None:
    if method == "GET":
        resp = await _get(users_client, email_match="exact")
    else:
        resp = await _post(users_client, {"email_match": "exact"})
    _unprocessable(resp, "email_match", "email_required")


async def test_post_exact_hit(users_client: AsyncClient, db_session: AsyncSession) -> None:
    _, an, _, email = await _pair(db_session)
    resp = await _post(users_client, {"email": email, "email_match": "exact"})
    assert _emails(resp) == [an.email]


async def test_post_exact_case_insensitive_and_trimmed(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, an, _, email = await _pair(db_session)
    resp = await _post(users_client, {"email": f" {email.upper()} ", "email_match": "exact"})
    assert _emails(resp) == [an.email]


async def test_post_exact_no_partial_hit(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    tag, _, joan, email = await _pair(db_session)
    resp = await _post(users_client, {"email": email, "email_match": "exact"})
    assert joan.email not in _emails(resp)
    assert _emails(await _post(users_client, {"email": f"n@{tag}", "email_match": "exact"})) == []


async def test_post_default_is_contains(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, an, joan, email = await _pair(db_session)
    assert set(_emails(await _post(users_client, {"email": email}))) == {an.email, joan.email}


async def test_post_parity_with_get(users_client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, f"srch-{uuid.uuid4().hex[:6]}")
    role = await make_role(db_session, app, f"{app.key}:viewer")
    tag = f"{uuid.uuid4().hex[:8]}.test"
    users = [await _user(db_session, f"{n}@{tag}") for n in ("a1", "a2", "a3")]
    for u in users:
        await assign(db_session, u, role)
    get = await users_client.get(
        BASE,
        params={
            "email": f"@{tag}",
            "email_match": "contains",
            "status": "active",
            "app": app.key,
            "role_id": str(role.id),
            "include": "access",
            "limit": 1,
            "offset": 1,
        },
    )
    post = await _post(
        users_client,
        {
            "email": f"@{tag}",
            "email_match": "contains",
            "status": "active",
            "app": app.key,
            "role_id": str(role.id),
            "include": ["access"],
            "limit": 1,
            "offset": 1,
        },
    )
    assert get.status_code == post.status_code == 200
    assert post.json() == get.json()
    assert post.json()["total"] == 3
    assert len(post.json()["items"]) == 1


async def test_post_include_access_shape(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, _, _, email = await _pair(db_session)
    plain = (await _post(users_client, {"email": email, "email_match": "exact"})).json()
    assert "access" not in plain["items"][0]
    with_access = (
        await _post(users_client, {"email": email, "email_match": "exact", "include": ["access"]})
    ).json()
    item = with_access["items"][0]
    assert item["access"] == [] and item["access_truncated"] is False


async def test_post_empty_body_lists_all(
    users_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _pair(db_session)
    resp = await _post(users_client, {})
    assert resp.status_code == 200
    assert (resp.json()["limit"], resp.json()["offset"]) == (20, 0)


@pytest.mark.parametrize("body", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_post_pagination_bounds(users_client: AsyncClient, body: dict[str, Any]) -> None:
    resp = await _post(users_client, body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert resp.json()["error"]["details"][0]["loc"][0] == "body"


async def test_post_unknown_app_422(users_client: AsyncClient) -> None:
    _unprocessable(await _post(users_client, {"app": "no-such-app"}), "app", "unknown_app")


async def test_post_unknown_role_422(users_client: AsyncClient) -> None:
    resp = await _post(users_client, {"role_id": str(uuid.uuid4())})
    _unprocessable(resp, "role_id", "unknown_role")


@pytest.mark.parametrize(
    "raw",
    [
        b"{not json",
        b'{"email": "secret-1@x.test", "bogus": 1}',
        b'{"email": ""}',
        b'{"email": "' + b"s" * 255 + b'"}',
        b'{"app": "Bad Key secret-2"}',
        b'{"role_id": "secret-3"}',
        b'{"include": ["secret-4"]}',
    ],
)
async def test_post_malformed_body_422(users_client: AsyncClient, raw: bytes) -> None:
    resp = await users_client.post(
        SEARCH, content=raw, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"
    assert "secret-" not in resp.text
    assert "s" * 255 not in resp.text


async def test_post_requires_content_length_411(users_client: AsyncClient) -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield b"{}"

    resp = await users_client.post(
        SEARCH, content=stream(), headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 411
    assert resp.json()["error"]["code"] == "length_required"


async def test_post_body_too_large_413(users_client: AsyncClient) -> None:
    resp = await users_client.post(
        SEARCH,
        content=b'{"email": "' + b"a" * 5000 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


async def test_post_wrong_content_type_415(users_client: AsyncClient) -> None:
    resp = await users_client.post(SEARCH, content=b"{}", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "unsupported_media_type"


async def test_post_email_not_logged(
    users_client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    email = f"needle-{uuid.uuid4().hex}@x.test"
    await _user(db_session, email)
    with caplog.at_level(logging.DEBUG):
        resp = await _post(users_client, {"email": email, "email_match": "exact"})
    assert _emails(resp) == [email]
    assert resp.request.url.query == b""
    assert str(resp.request.url) == f"http://test{SEARCH}"
    assert all(email not in r.getMessage() for r in caplog.records)


async def test_post_writes_no_audit(users_client: AsyncClient, db_session: AsyncSession) -> None:
    _, _, _, email = await _pair(db_session)
    before = await db_session.scalar(select(func.count()).select_from(AuditLog))
    assert (await _post(users_client, {"email": email})).status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == before


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_search_requires_token(make_client: MakeClient, method: str) -> None:
    async with make_client() as c:
        if method == "GET":
            resp = await _get(c, email="a@b.test", email_match="exact")
        else:
            resp = await _post(c, {"email": "a@b.test"})
    assert resp.status_code == 401


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_search_requires_users_read(make_client: MakeClient, method: str) -> None:
    async def call(c: AsyncClient) -> Response:
        if method == "GET":
            return await _get(c, email="a@b.test", email_match="exact")
        return await _post(c, {"email": "a@b.test", "email_match": "exact"})

    async with make_client(admin_permissions=["admin:orgs:read"]) as c:
        resp = await call(c)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"
    async with make_client(admin_permissions=["admin:users:read"]) as c:
        assert (await call(c)).status_code == 200


async def test_openapi_search_contract(users_client: AsyncClient) -> None:
    spec = (await users_client.get("/openapi.json")).json()
    get_params = {p["name"]: p for p in spec["paths"][BASE]["get"]["parameters"]}
    ref = get_params["email_match"]["schema"]["anyOf"][0]["$ref"].rsplit("/", 1)[1]
    assert spec["components"]["schemas"][ref]["enum"] == ["exact", "contains"]
    post = spec["paths"][SEARCH]["post"]
    schema = post["requestBody"]["content"]["application/json"]["schema"]
    assert {
        "email", "email_match", "status", "app", "role_id", "include", "limit", "offset"
    } <= set(schema["properties"])  # fmt: skip
    assert post["x-permission"] == "admin:users:read"
    assert "200" in post["responses"] and "201" not in post["responses"]
