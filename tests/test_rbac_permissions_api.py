import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, Permission, Role
from app.modules.rbac.repository import RbacRepository
from tests.conftest import MakeClient
from tests.rbac_factories import grant, make_app, make_permission, make_role

BASE = "/api/v1/apps"
URL = f"{BASE}/stoktok/permissions"
PERMISSION_READ_KEYS = {"id", "key", "description", "is_admin"}


@pytest.fixture
async def rbac_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_rbac_api_enabled=True) as c:
        yield c


async def _perm(db_session: AsyncSession, key: str) -> Permission | None:
    perm = await db_session.scalar(select(Permission).where(Permission.key == key))
    if perm is not None:
        await db_session.refresh(perm)
    return perm


async def _count(db_session: AsyncSession) -> int:
    return await db_session.scalar(select(func.count()).select_from(Permission)) or 0


async def _create_app_with_member(rbac_client: AsyncClient, db_session: AsyncSession) -> Role:
    """POST /apps builds `stoktok:member` as the default role."""
    assert (await rbac_client.post(BASE, json={"key": "stoktok", "name": "S"})).status_code == 201
    role = await db_session.scalar(select(Role).where(Role.key == "stoktok:member"))
    assert role is not None
    return role


# --- exposure ----------------------------------------------------------------


async def test_permissions_api_not_mounted_by_default(client: AsyncClient) -> None:
    pid = uuid.uuid4()
    assert (await client.get(URL)).status_code == 404
    assert (await client.post(URL, json={"key": "stoktok:a:b"})).status_code == 404
    assert (await client.patch(f"{URL}/{pid}", json={})).status_code == 404
    assert (await client.delete(f"{URL}/{pid}")).status_code == 404


# --- list --------------------------------------------------------------------


async def test_list_permissions_paginated_and_sorted(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    other = await make_app(db_session, "calechip")
    for key in ("stoktok:c:read", "stoktok:a:read", "stoktok:b:read"):
        await make_permission(db_session, app, key)
    await make_permission(db_session, other, "calechip:x:read")

    resp = await rbac_client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert [p["key"] for p in body["items"]] == [
        "stoktok:a:read",
        "stoktok:b:read",
        "stoktok:c:read",
    ]
    assert body["total"] == 3

    page = (await rbac_client.get(URL, params={"limit": 1, "offset": 1})).json()
    assert [p["key"] for p in page["items"]] == ["stoktok:b:read"]
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)

    assert (await rbac_client.get(URL, params={"limit": 101})).status_code == 422


async def test_list_permissions_unknown_app_404(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.get(f"{BASE}/nope/permissions")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_permission_read_has_exact_keys(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok")
    created = await rbac_client.post(URL, json={"key": "stoktok:a:read"})
    assert set(created.json()) == PERMISSION_READ_KEYS
    listed = await rbac_client.get(URL)
    assert set(listed.json()["items"][0]) == PERMISSION_READ_KEYS
    patched = await rbac_client.patch(f"{URL}/{created.json()['id']}", json={"description": "d"})
    assert set(patched.json()) == PERMISSION_READ_KEYS


# --- create ------------------------------------------------------------------


async def test_create_permission(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    await make_app(db_session, "stoktok")
    resp = await rbac_client.post(URL, json={"key": "stoktok:a:read"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["key"] == "stoktok:a:read"
    assert body["description"] is None
    assert body["is_admin"] is False

    resp = await rbac_client.post(
        URL, json={"key": "stoktok:roles:manage", "description": "Manage", "is_admin": True}
    )
    assert resp.status_code == 201
    assert resp.json()["is_admin"] is True
    perm = await _perm(db_session, "stoktok:roles:manage")
    assert perm is not None and perm.is_admin and perm.description == "Manage"


@pytest.mark.parametrize("body", [{"key": "stoktok:*"}, {"key": "stoktok:*", "is_admin": None}])
async def test_create_wildcard_forced_admin(
    rbac_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    await make_app(db_session, "stoktok")
    resp = await rbac_client.post(URL, json=body)
    assert resp.status_code == 201
    assert resp.json()["is_admin"] is True


async def test_create_wildcard_not_admin_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok")
    resp = await rbac_client.post(URL, json={"key": "stoktok:*", "is_admin": False})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "unprocessable"
    assert error["details"] == [{"field": "is_admin", "reason": "wildcard_must_be_admin"}]
    assert await _perm(db_session, "stoktok:*") is None


@pytest.mark.parametrize("key", ["calechip:x:read", "admin:x:read"])
async def test_create_permission_prefix_mismatch_422(
    rbac_client: AsyncClient, db_session: AsyncSession, key: str
) -> None:
    await make_app(db_session, "stoktok")
    await make_app(db_session, "calechip")
    before = await _count(db_session)
    resp = await rbac_client.post(URL, json={"key": key})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "unprocessable"
    assert error["details"] == [{"field": "key", "reason": "prefix_mismatch"}]
    assert await _count(db_session) == before


@pytest.mark.parametrize(
    "body",
    [
        {"key": "Stoktok:x:y"},
        {"key": "stoktok:x"},
        {"key": "stoktok:x:*"},
        {"key": "*"},
        {},
        {"key": "stoktok:x:y", "description": "d" * 501},
        {"key": "stoktok:x:y", "description": "a\x00b"},
        {"key": "stoktok:x:y", "description": "a\nb"},
        {"key": "stoktok:x:y", "is_system": True},
        {"key": "stoktok:x:y", "id": str(uuid.uuid4())},
        {"key": "stoktok:x:y", "app_id": str(uuid.uuid4())},
    ],
)
async def test_create_permission_validation_422(
    rbac_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    await make_app(db_session, "stoktok")
    before = await _count(db_session)
    resp = await rbac_client.post(URL, json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert await _count(db_session) == before


async def test_create_permission_duplicate_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok")
    assert (await rbac_client.post(URL, json={"key": "stoktok:a:read"})).status_code == 201
    resp = await rbac_client.post(URL, json={"key": "stoktok:a:read"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_create_permission_unknown_app_404(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.post(f"{BASE}/nope/permissions", json={"key": "nope:a:read"})
    assert resp.status_code == 404


async def test_create_permission_integrity_race_is_409(
    rbac_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = await make_app(db_session, "stoktok")
    await make_permission(db_session, app, "stoktok:a:read")

    async def _missing(self: RbacRepository, app_id: uuid.UUID, key: str) -> Permission | None:
        return None

    monkeypatch.setattr(RbacRepository, "get_permission_by_key", _missing)
    resp = await rbac_client.post(URL, json={"key": "stoktok:a:read"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    # The savepoint kept the session usable.
    assert await db_session.scalar(select(func.count()).select_from(App)) == 2


# --- patch -------------------------------------------------------------------


async def test_patch_permission(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:read")
    url = f"{URL}/{perm.id}"

    resp = await rbac_client.patch(url, json={"description": "Read A"})
    assert resp.status_code == 200 and resp.json()["description"] == "Read A"
    resp = await rbac_client.patch(url, json={"description": None})
    assert resp.status_code == 200 and resp.json()["description"] is None
    for value in (True, False):
        resp = await rbac_client.patch(url, json={"is_admin": value})
        assert resp.status_code == 200 and resp.json()["is_admin"] is value

    before = resp.json()
    resp = await rbac_client.patch(url, json={})
    assert resp.status_code == 200
    assert resp.json() == before


async def test_patch_permission_null_is_admin_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:read")
    resp = await rbac_client.patch(f"{URL}/{perm.id}", json={"is_admin": None})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("body", [{"key": "stoktok:b:read"}, {"is_system": True}])
async def test_patch_permission_rejects_key(
    rbac_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:read")
    resp = await rbac_client.patch(f"{URL}/{perm.id}", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    refreshed = await _perm(db_session, "stoktok:a:read")
    assert refreshed is not None


async def test_patch_wildcard_not_admin_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok")
    created = await rbac_client.post(URL, json={"key": "stoktok:*"})
    admin_wildcard = await _perm(db_session, "admin:*")
    assert admin_wildcard is not None
    for url in (
        f"{URL}/{created.json()['id']}",
        f"{BASE}/admin/permissions/{admin_wildcard.id}",
    ):
        resp = await rbac_client.patch(url, json={"is_admin": False})
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "unprocessable"
        assert error["details"] == [{"field": "is_admin", "reason": "wildcard_must_be_admin"}]
    for key in ("stoktok:*", "admin:*"):
        perm = await _perm(db_session, key)
        assert perm is not None and perm.is_admin


async def test_patch_permission_admin_while_default_holds_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    member = await _create_app_with_member(rbac_client, db_session)
    created = await rbac_client.post(URL, json={"key": "stoktok:a:read"})
    put = await rbac_client.put(
        f"{BASE}/stoktok/roles/{member.id}/permissions",
        json={"permission_keys": ["stoktok:a:read"]},
    )
    assert put.status_code == 200

    resp = await rbac_client.patch(f"{URL}/{created.json()['id']}", json={"is_admin": True})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    perm = await _perm(db_session, "stoktok:a:read")
    assert perm is not None and perm.is_admin is False


async def test_patch_permission_404(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    other = await make_app(db_session, "calechip")
    perm = await make_permission(db_session, app, "stoktok:a:read")
    foreign = await make_permission(db_session, other, "calechip:x:read")

    assert (
        await rbac_client.patch(f"{BASE}/nope/permissions/{perm.id}", json={})
    ).status_code == 404
    assert (await rbac_client.patch(f"{URL}/{uuid.uuid4()}", json={})).status_code == 404
    assert (await rbac_client.patch(f"{URL}/{foreign.id}", json={})).status_code == 404
    resp = await rbac_client.patch(f"{URL}/not-a-uuid", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- delete ------------------------------------------------------------------


async def test_delete_permission(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:read")
    resp = await rbac_client.delete(f"{URL}/{perm.id}")
    assert resp.status_code == 204
    assert await db_session.scalar(select(Permission).where(Permission.id == perm.id)) is None
    assert (await rbac_client.get(URL)).json()["items"] == []


async def test_delete_admin_wildcard_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    perm = await _perm(db_session, "admin:*")
    assert perm is not None
    resp = await rbac_client.delete(f"{BASE}/admin/permissions/{perm.id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert await _perm(db_session, "admin:*") is not None


async def test_delete_permission_held_by_role_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    perm = await make_permission(db_session, app, "stoktok:a:read")
    role = await make_role(db_session, app, "stoktok:viewer")
    await grant(db_session, role, perm)

    resp = await rbac_client.delete(f"{URL}/{perm.id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert await _perm(db_session, "stoktok:a:read") is not None
    assert await RbacRepository(db_session).role_holds_permission(role.id, perm.id)


async def test_delete_permission_404(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    other = await make_app(db_session, "calechip")
    perm = await make_permission(db_session, app, "stoktok:a:read")
    foreign = await make_permission(db_session, other, "calechip:x:read")

    assert (await rbac_client.delete(f"{BASE}/nope/permissions/{perm.id}")).status_code == 404
    assert (await rbac_client.delete(f"{URL}/{uuid.uuid4()}")).status_code == 404
    assert (await rbac_client.delete(f"{URL}/{foreign.id}")).status_code == 404
    assert await _perm(db_session, "calechip:x:read") is not None
