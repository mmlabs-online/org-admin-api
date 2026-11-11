import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.main import create_app
from app.modules.rbac.models import App, Permission, Role
from app.modules.rbac.repository import RbacRepository
from tests.conftest import MakeClient
from tests.rbac_factories import assign, grant, make_app, make_permission, make_role, make_user

BASE = "/api/v1/apps"
APP_READ_KEYS = {
    "id",
    "key",
    "name",
    "status",
    "join_policy",
    "org_mode",
    "origins",
    "default_role_id",
    "created_at",
    "updated_at",
}


@pytest.fixture
async def rbac_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_rbac_api_enabled=True) as c:
        yield c


async def _app_row(db_session: AsyncSession, key: str) -> App | None:
    app = await db_session.scalar(select(App).where(App.key == key))
    if app is not None:
        await db_session.refresh(app)
    return app


# --- exposure ----------------------------------------------------------------


async def test_apps_api_not_mounted_by_default(client: AsyncClient) -> None:
    assert (await client.get(BASE)).status_code == 404
    assert (await client.post(BASE, json={"key": "stoktok", "name": "S"})).status_code == 404
    assert (await client.get(f"{BASE}/stoktok")).status_code == 404
    assert (await client.patch(f"{BASE}/stoktok", json={"name": "x"})).status_code == 404
    assert (await client.delete(f"{BASE}/stoktok")).status_code == 404


def test_rbac_routes_declare_permissions() -> None:
    # FastAPI wraps included routers lazily, so enumerate operations via the OpenAPI schema.
    app = create_app(Settings(admin_rbac_api_enabled=True))
    expected = {
        ("GET", "/api/v1/apps"): "admin:apps:read",
        ("POST", "/api/v1/apps"): "admin:apps:write",
        ("GET", "/api/v1/apps/{key}"): "admin:apps:read",
        ("PATCH", "/api/v1/apps/{key}"): "admin:apps:write",
        ("DELETE", "/api/v1/apps/{key}"): "admin:apps:write",
    }
    found = {
        (method.upper(), path): operation.get("x-permission")
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/apps")
        for method, operation in operations.items()
    }
    assert found == expected


# --- list / get --------------------------------------------------------------


async def test_list_apps_paginated_and_sorted(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok")
    await make_app(db_session, "calechip")  # `admin` is seeded by the migrations

    resp = await rbac_client.get(BASE)
    assert resp.status_code == 200
    body = resp.json()
    assert [i["key"] for i in body["items"]] == ["admin", "calechip", "stoktok"]
    assert body["total"] == 3

    resp = await rbac_client.get(BASE, params={"limit": 1, "offset": 1})
    assert [i["key"] for i in resp.json()["items"]] == ["calechip"]
    assert resp.json()["total"] == 3

    assert (await rbac_client.get(BASE, params={"limit": 101})).status_code == 422


async def test_get_app(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "stoktok")
    resp = await rbac_client.get(f"{BASE}/stoktok")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(app.id)
    assert resp.json()["key"] == "stoktok"


async def test_get_app_unknown_404(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.get(f"{BASE}/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_app_read_has_exact_keys(rbac_client: AsyncClient) -> None:
    created = await rbac_client.post(BASE, json={"key": "stoktok", "name": "Stoktok"})
    assert set(created.json()) == APP_READ_KEYS
    listed = await rbac_client.get(BASE)
    assert all(set(item) == APP_READ_KEYS for item in listed.json()["items"])
    got = await rbac_client.get(f"{BASE}/stoktok")
    assert set(got.json()) == APP_READ_KEYS
    patched = await rbac_client.patch(f"{BASE}/stoktok", json={"name": "S"})
    assert set(patched.json()) == APP_READ_KEYS


# --- create ------------------------------------------------------------------


async def test_create_app_builds_member_role(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await rbac_client.post(BASE, json={"key": "stoktok", "name": "Stoktok"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["join_policy"] == "open"
    assert body["org_mode"] == "none"
    assert body["status"] == "active"
    assert body["origins"] == []
    role = await db_session.scalar(select(Role).where(Role.key == "stoktok:member"))
    assert role is not None and role.is_system
    assert body["default_role_id"] == str(role.id)
    held = await RbacRepository(db_session).role_permission_keys(role.id)
    assert held == {}


async def test_create_app_duplicate_key_409(rbac_client: AsyncClient) -> None:
    assert (await rbac_client.post(BASE, json={"key": "stoktok", "name": "S"})).status_code == 201
    resp = await rbac_client.post(BASE, json={"key": "stoktok", "name": "S"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    resp = await rbac_client.post(BASE, json={"key": "admin", "name": "A"})
    assert resp.status_code == 409


async def test_create_app_integrity_race_is_409(
    rbac_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_app(db_session, "stoktok")

    async def _missing(self: RbacRepository, key: str) -> App | None:
        return None

    monkeypatch.setattr(RbacRepository, "get_app_by_key", _missing)
    resp = await rbac_client.post(BASE, json={"key": "stoktok", "name": "S"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    # The savepoint kept the session usable.
    assert await db_session.scalar(select(func.count()).select_from(App)) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"key": "Bad", "name": "x"},
        {"key": "a", "name": "x"},
        {"key": "st:ok", "name": "x"},
        {"key": "1app", "name": "x"},
        {"key": "a" * 33, "name": "x"},
        {"key": "stoktok"},
        {"key": "stoktok", "name": ""},
        {"key": "stoktok", "name": "x", "join_policy": "x"},
        {"key": "stoktok", "name": "x", "org_mode": "x"},
        {"key": "stoktok", "name": "x", "status": "active"},
        {"key": "stoktok", "name": "x", "default_role_id": str(uuid.uuid4())},
        {"key": "stoktok", "name": "x", "is_system": True},
        {"key": "stoktok", "name": "x", "id": str(uuid.uuid4())},
    ],
)
async def test_create_app_validation_422(rbac_client: AsyncClient, body: dict[str, Any]) -> None:
    resp = await rbac_client.post(BASE, json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_create_app_origins_normalized(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.post(
        BASE,
        json={
            "key": "stoktok",
            "name": "S",
            "origins": ["https://Stoktok.MMLabs.online/", "https://stoktok.mmlabs.online:443"],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["origins"] == ["https://stoktok.mmlabs.online"]


# --- patch -------------------------------------------------------------------


async def test_patch_app_fields(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    await make_app(db_session, "stoktok")
    url = f"{BASE}/stoktok"
    changes: list[tuple[str, Any]] = [
        ("name", "Stoktok 2"),
        ("status", "disabled"),
        ("join_policy", "approval"),
        ("org_mode", "required"),
        ("origins", ["https://stoktok.mmlabs.online"]),
    ]
    for field, value in changes:
        resp = await rbac_client.patch(url, json={field: value})
        assert resp.status_code == 200
        assert resp.json()[field] == value
    before = (await rbac_client.get(url)).json()
    resp = await rbac_client.patch(url, json={})
    assert resp.status_code == 200
    assert resp.json() == before


@pytest.mark.parametrize("body", [{"key": "x"}, {"id": str(uuid.uuid4())}, {"foo": 1}])
async def test_patch_app_rejects_key_and_unknown_fields(
    rbac_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    await make_app(db_session, "stoktok")
    resp = await rbac_client.patch(f"{BASE}/stoktok", json=body)
    assert resp.status_code == 422
    assert (await rbac_client.get(f"{BASE}/stoktok")).status_code == 200


@pytest.mark.parametrize("field", ["name", "status", "join_policy", "org_mode", "origins"])
async def test_patch_app_null_only_for_default_role(
    rbac_client: AsyncClient, db_session: AsyncSession, field: str
) -> None:
    await make_app(db_session, "stoktok")
    resp = await rbac_client.patch(f"{BASE}/stoktok", json={field: None})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_patch_app_bad_origin_422(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    await make_app(db_session, "stoktok")
    resp = await rbac_client.patch(f"{BASE}/stoktok", json={"origins": ["http://example.com"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert "example.com" not in resp.text


async def test_patch_unknown_app_404(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.patch(f"{BASE}/nope", json={"name": "x"})
    assert resp.status_code == 404


async def test_patch_default_role_set_and_clear(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    viewer = await make_role(db_session, app, "stoktok:viewer")
    await grant(db_session, viewer, await make_permission(db_session, app, "stoktok:news:read"))

    resp = await rbac_client.patch(f"{BASE}/stoktok", json={"default_role_id": str(viewer.id)})
    assert resp.status_code == 200
    assert resp.json()["default_role_id"] == str(viewer.id)

    resp = await rbac_client.patch(f"{BASE}/stoktok", json={"default_role_id": None})
    assert resp.status_code == 200
    assert resp.json()["default_role_id"] is None


async def test_patch_default_role_unknown_or_foreign_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok")
    calechip = await make_app(db_session, "calechip")
    foreign = await make_role(db_session, calechip, "calechip:viewer")
    for role_id in (uuid.uuid4(), foreign.id):
        resp = await rbac_client.patch(f"{BASE}/stoktok", json={"default_role_id": str(role_id)})
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "unprocessable"
        assert error["details"] == [{"field": "default_role_id", "reason": "unknown_role"}]


async def test_patch_default_role_with_admin_permission_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    managers = await make_role(db_session, app, "stoktok:managers")
    perm = await make_permission(db_session, app, "stoktok:roles:manage", is_admin=True)
    await grant(db_session, managers, perm)

    resp = await rbac_client.patch(f"{BASE}/stoktok", json={"default_role_id": str(managers.id)})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unprocessable"
    row = await _app_row(db_session, "stoktok")
    assert row is not None and row.default_role_id is None


# --- admin app protections ---------------------------------------------------


async def test_admin_app_cannot_be_deleted(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.delete(f"{BASE}/admin")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert (await rbac_client.get(f"{BASE}/admin")).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"join_policy": "open"},
        {"join_policy": "approval"},
        {"default_role_id": "SUPER_ADMIN"},
        {"status": "disabled"},
    ],
)
async def test_admin_app_locked_fields_409(
    rbac_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    if body.get("default_role_id") == "SUPER_ADMIN":
        role = await db_session.scalar(select(Role).where(Role.key == "admin:super-admin"))
        assert role is not None
        body = {"default_role_id": str(role.id)}
    before = (await rbac_client.get(f"{BASE}/admin")).json()

    resp = await rbac_client.patch(f"{BASE}/admin", json=body)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert (await rbac_client.get(f"{BASE}/admin")).json() == before


async def test_admin_app_same_value_patch_ok(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.patch(
        f"{BASE}/admin",
        json={
            "join_policy": "closed",
            "status": "active",
            "default_role_id": None,
            "name": "mmlabs Admin",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["join_policy"], body["status"], body["default_role_id"]) == (
        "closed",
        "active",
        None,
    )


# --- delete ------------------------------------------------------------------


async def test_delete_app(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    assert (await rbac_client.post(BASE, json={"key": "stoktok", "name": "S"})).status_code == 201
    app = await _app_row(db_session, "stoktok")
    assert app is not None
    await make_permission(db_session, app, "stoktok:news:read")
    app_id = app.id

    resp = await rbac_client.delete(f"{BASE}/stoktok")
    assert resp.status_code == 204
    assert (await rbac_client.get(f"{BASE}/stoktok")).status_code == 404
    roles = select(func.count()).select_from(Role).where(Role.app_id == app_id)
    perms = select(func.count()).select_from(Permission).where(Permission.app_id == app_id)
    assert await db_session.scalar(roles) == 0
    assert await db_session.scalar(perms) == 0


async def test_delete_unknown_app_404(rbac_client: AsyncClient) -> None:
    assert (await rbac_client.delete(f"{BASE}/nope")).status_code == 404


async def test_delete_app_with_assignments_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "stoktok")
    role = await make_role(db_session, app, "stoktok:viewer")
    await assign(db_session, await make_user(db_session), role)

    resp = await rbac_client.delete(f"{BASE}/stoktok")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert (await rbac_client.get(f"{BASE}/stoktok")).status_code == 200
