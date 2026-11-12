"""Public role catalog `GET /api/v1/apps/{key}/roles` (USR-02e, D17)."""

import hashlib
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.main import create_app
from app.modules.rbac.models import App, Role, RolePermission
from tests.conftest import MakeClient
from tests.rbac_factories import grant, make_app, make_permission

URL = "/api/v1/apps/stoktok/roles"


async def _role(db_session: AsyncSession, app_id: uuid.UUID, key: str, name: str) -> Role:
    role = Role(app_id=app_id, key=key, name=name)
    db_session.add(role)
    await db_session.flush()
    return role


async def _setup(db_session: AsyncSession) -> tuple[App, Role, Role]:
    """`stoktok` with roles inserted out of key order and permissions granted out of order."""
    app = await make_app(db_session, "stoktok")
    write = await make_permission(db_session, app, "stoktok:portfolio:write")
    read = await make_permission(db_session, app, "stoktok:portfolio:read")
    owner = await _role(db_session, app.id, "stoktok:owner", "Chủ sở hữu")
    member = await _role(db_session, app.id, "stoktok:member", "Member")
    member.is_system = True
    await db_session.flush()
    await grant(db_session, owner, write, read)
    await grant(db_session, member, read)
    return app, owner, member


# --- document ----------------------------------------------------------------


async def test_catalog_document_shape_and_order(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _setup(db_session)
    resp = await client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {
        "app": "stoktok",
        "roles": [
            {
                "key": "stoktok:member",
                "name": "Member",
                "is_system": True,
                "permissions": ["stoktok:portfolio:read"],
            },
            {
                "key": "stoktok:owner",
                "name": "Chủ sở hữu",
                "is_system": False,
                "permissions": ["stoktok:portfolio:read", "stoktok:portfolio:write"],
            },
        ],
    }
    # Compact UTF-8 bytes (non-ASCII not escaped).
    assert resp.content.startswith(b'{"app":"stoktok","roles":[{"key":"stoktok:member"')
    assert "Chủ sở hữu".encode() in resp.content


async def test_catalog_exact_key_set(client: AsyncClient, db_session: AsyncSession) -> None:
    await _setup(db_session)
    body = (await client.get(URL)).json()
    assert set(body) == {"app", "roles"}
    for role in body["roles"]:
        assert set(role) == {"key", "name", "is_system", "permissions"}
        assert all(isinstance(p, str) for p in role["permissions"])


async def test_catalog_empty_app_and_role_without_permissions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    assert (await client.get(URL)).json() == {"app": "stoktok", "roles": []}

    await _role(db_session, app.id, "stoktok:viewer", "Viewer")
    assert (await client.get(URL)).json() == {
        "app": "stoktok",
        "roles": [
            {"key": "stoktok:viewer", "name": "Viewer", "is_system": False, "permissions": []}
        ],
    }


# --- ETag --------------------------------------------------------------------


async def test_catalog_etag_is_hash_of_body(client: AsyncClient, db_session: AsyncSession) -> None:
    await _setup(db_session)
    resp = await client.get(URL)
    assert resp.headers["etag"] == '"' + hashlib.sha256(resp.content).hexdigest()[:32] + '"'
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["content-type"] == "application/json"


async def test_catalog_etag_stable(client: AsyncClient, db_session: AsyncSession) -> None:
    await _setup(db_session)
    first = await client.get(URL)
    second = await client.get(URL)
    assert first.headers["etag"] == second.headers["etag"]
    assert first.content == second.content


async def test_catalog_etag_changes_after_change(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, owner, member = await _setup(db_session)
    tags = [(await client.get(URL)).headers["etag"]]

    async def _changed() -> None:
        tag = (await client.get(URL)).headers["etag"]
        assert tag != tags[-1]
        tags.append(tag)

    extra = await make_permission(db_session, app, "stoktok:alerts:read")
    await grant(db_session, member, extra)  # grant
    await _changed()

    await db_session.execute(
        delete(RolePermission).where(
            RolePermission.role_id == member.id, RolePermission.permission_id == extra.id
        )
    )  # revoke
    await _changed()
    assert tags[-1] == tags[0]  # content-addressed: back to the original document

    owner.name = "Owner"  # rename
    await db_session.flush()
    await _changed()

    viewer = await _role(db_session, app.id, "stoktok:viewer", "Viewer")  # new role
    await _changed()

    await db_session.delete(viewer)  # role delete
    await db_session.flush()
    await _changed()


# --- If-None-Match -----------------------------------------------------------


@pytest.mark.parametrize("variant", ["exact", "weak", "list", "star"])
async def test_catalog_304_variants(
    client: AsyncClient, db_session: AsyncSession, variant: str
) -> None:
    await _setup(db_session)
    etag = (await client.get(URL)).headers["etag"]
    header = {"exact": etag, "weak": f"W/{etag}", "list": f'"x", {etag}', "star": "*"}[variant]
    resp = await client.get(URL, headers={"If-None-Match": header})
    assert resp.status_code == 304


async def test_catalog_304_response_shape(client: AsyncClient, db_session: AsyncSession) -> None:
    await _setup(db_session)
    etag = (await client.get(URL)).headers["etag"]
    resp = await client.get(URL, headers={"If-None-Match": etag})
    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["etag"] == etag
    assert resp.headers["cache-control"] == "no-cache"
    assert "content-type" not in resp.headers
    assert resp.headers.get("content-length", "0") == "0"


@pytest.mark.parametrize("header", ['"0000000000000000000000000000000"', "not-quoted", "W/x"])
async def test_catalog_if_none_match_no_match_200(
    client: AsyncClient, db_session: AsyncSession, header: str
) -> None:
    await _setup(db_session)
    resp = await client.get(URL, headers={"If-None-Match": header})
    assert resp.status_code == 200
    assert resp.json()["app"] == "stoktok"


# --- 404 ---------------------------------------------------------------------


async def test_catalog_404_cases_identical(client: AsyncClient, db_session: AsyncSession) -> None:
    await make_app(db_session, "calechip", status="disabled")
    bodies = set()
    for key in ("unknown", "calechip", "admin", "UPPER", "a" * 40):
        resp = await client.get(f"/api/v1/apps/{key}/roles")
        assert resp.status_code == 404, key
        assert resp.json()["error"]["code"] == "not_found"
        assert "etag" not in resp.headers
        bodies.add(resp.content)
    assert len(bodies) == 1


# --- mounting ----------------------------------------------------------------


async def test_catalog_reachable_flag_off(client: AsyncClient, db_session: AsyncSession) -> None:
    await _setup(db_session)
    assert (await client.get(URL)).status_code == 200


async def test_catalog_reachable_flag_on_no_clash(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await _setup(db_session)
    async with make_client(admin_rbac_api_enabled=True) as c:
        resp = await c.get(URL)
        assert resp.status_code == 200
        assert set(resp.json()) == {"app", "roles"}
        created = await c.post(URL, json={"key": "stoktok:editor", "name": "Editor"})
        assert created.status_code == 201
        assert "id" in created.json()


async def test_post_roles_flag_off_is_405(client: AsyncClient, db_session: AsyncSession) -> None:
    await make_app(db_session, "stoktok")
    resp = await client.post(URL, json={"key": "stoktok:editor", "name": "Editor"})
    assert resp.status_code == 405
    assert resp.json() == {
        "error": {"code": "method_not_allowed", "message": "Method Not Allowed", "details": None}
    }
    assert "GET" in resp.headers["Allow"]


async def test_encoded_slash_key_404_envelope(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/apps/a%2Fb/roles")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert "detail" not in resp.json()


def test_only_catalog_route_listed_by_default() -> None:
    app = create_app(Settings())
    found = {
        (method.upper(), path): operation.get("x-permission")
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/apps")
        for method, operation in operations.items()
    }
    assert found == {("GET", "/api/v1/apps/{key}/roles"): None}


# --- rate limit --------------------------------------------------------------


async def test_catalog_rate_limited_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    await _setup(db_session)
    async with make_client(role_catalog_rate_limit=2, role_catalog_rate_window_seconds=60) as c:
        assert (await c.get(URL)).status_code == 200
        assert (await c.get(URL)).status_code == 200
        resp = await c.get(URL)
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "rate_limited"
        assert int(resp.headers["retry-after"]) >= 1


async def test_catalog_304_and_404_count_toward_limit(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    await _setup(db_session)
    async with make_client(role_catalog_rate_limit=3, role_catalog_rate_window_seconds=60) as c:
        etag = (await c.get(URL)).headers["etag"]  # 1: 200 (to learn the tag)
        assert (await c.get("/api/v1/apps/unknown/roles")).status_code == 404  # 2
        assert (await c.get(URL, headers={"If-None-Match": etag})).status_code == 304  # 3
        assert (await c.get(URL)).status_code == 429
