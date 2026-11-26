import unicodedata
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.listing import sort_clauses
from app.modules.audit.models import AuditLog
from app.modules.entitlements.models import OrgEntitlement
from app.modules.rbac.models import App, Permission, Role
from app.modules.rbac.repository import _APP_SORT_COLUMNS, RbacRepository
from app.modules.rbac.schemas import AppSort
from app.modules.rbac.service import RoleCatalogService
from tests.admin_auth_factories import bearer, make_staff, mint
from tests.auth_factories import make_login_user
from tests.conftest import MakeClient
from tests.rbac_factories import (
    assign,
    grant,
    make_app,
    make_org,
    make_permission,
    make_role,
    make_user,
)

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
    "org_owner_role_id",
    "super_admin_role_id",
    "created_at",
    "updated_at",
    "member_count",
}


@pytest.fixture
async def rbac_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"]) as c:
        yield c


async def _app_row(db_session: AsyncSession, key: str) -> App | None:
    app = await db_session.scalar(select(App).where(App.key == key))
    if app is not None:
        await db_session.refresh(app)
    return app


# Display-text filter (ADR-0003 §11): categories Cc, Cf, Zl, Zp, Co, Cn.
FORMAT_CHARS = [
    "\x00",
    "\x85",
    "\u202e",
    "\u2066",
    "\u2028",
    "\u200b",
    "\ufeff",
    "\u00ad",
    "\u200d",
    "\U000e0041",
    "\ue000",
    "\u0378",
    "\u180e",
]


@pytest.mark.parametrize("char", FORMAT_CHARS)
async def test_app_name_control_chars_422(
    rbac_client: AsyncClient, db_session: AsyncSession, char: str
) -> None:
    name = f"Marker{char}Name"
    resp = await rbac_client.post(BASE, json={"key": "stoktok", "name": name})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert "Marker" not in resp.text
    assert await _app_row(db_session, "stoktok") is None

    await make_app(db_session, "calechip")
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"name": name})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert "Marker" not in resp.text


async def test_app_name_stored_nfc(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    nfd, nfc = unicodedata.normalize("NFD", "Quản trị"), "Quản trị"
    assert nfd != nfc
    resp = await rbac_client.post(BASE, json={"key": "stoktok", "name": nfd})
    assert resp.status_code == 201
    assert resp.json()["name"] == nfc
    row = await _app_row(db_session, "stoktok")
    assert row is not None and row.name == nfc

    patched_nfd = unicodedata.normalize("NFD", "Chủ sở hữu")
    resp = await rbac_client.patch(f"{BASE}/stoktok", json={"name": patched_nfd})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Chủ sở hữu"
    row = await _app_row(db_session, "stoktok")
    assert row is not None and row.name == "Chủ sở hữu"


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


async def test_app_create_org_mode_required_builds_scoped_member(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await rbac_client.post(
        BASE, json={"key": "calechip", "name": "Calechip", "org_mode": "required"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["org_mode"] == "required" and body["default_role_id"] is not None
    perms = f"{BASE}/calechip/permissions"
    for key, scope, is_admin in (
        ("calechip:a:read", "app", False),
        ("calechip:b:read", "org", False),
        ("calechip:c:manage", "app", True),
    ):
        created = await rbac_client.post(
            perms, json={"key": key, "scope": scope, "is_admin": is_admin}
        )
        assert created.status_code == 201
    await RoleCatalogService(db_session).build_default_role("calechip")
    held = await RbacRepository(db_session).role_permission_keys(uuid.UUID(body["default_role_id"]))
    assert set(held.values()) == {"calechip:a:read"}


async def test_create_app_duplicate_key_409(rbac_client: AsyncClient) -> None:
    assert (await rbac_client.post(BASE, json={"key": "stoktok", "name": "S"})).status_code == 201
    resp = await rbac_client.post(BASE, json={"key": "stoktok", "name": "S"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "app_key_taken"
    assert resp.json()["error"]["details"] == [{"field": "key", "reason": "app_key_taken"}]
    resp = await rbac_client.post(BASE, json={"key": "admin", "name": "A"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "app_key_taken"
    assert resp.json()["error"]["details"] == [{"field": "key", "reason": "app_key_taken"}]


async def test_create_app_integrity_race_is_409(
    rbac_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_app(db_session, "stoktok")

    async def _missing(self: RbacRepository, key: str) -> App | None:
        return None

    monkeypatch.setattr(RbacRepository, "get_app_by_key", _missing)
    resp = await rbac_client.post(BASE, json={"key": "stoktok", "name": "S"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "app_key_taken"
    assert resp.json()["error"]["details"] == [{"field": "key", "reason": "app_key_taken"}]
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


async def test_patch_app_non_mmlabs_origin_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok", origins=["https://stoktok.mmlabs.online"])
    resp = await rbac_client.patch(f"{BASE}/stoktok", json={"origins": ["https://example.com"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert "example.com" not in resp.text
    app = await _app_row(db_session, "stoktok")
    assert app is not None and app.origins == ["https://stoktok.mmlabs.online"]


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
        {"org_mode": "required"},
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
            "org_mode": "none",
            "name": "mmlabs Admin",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["join_policy"], body["status"], body["default_role_id"], body["org_mode"]) == (
        "closed",
        "active",
        None,
        "none",
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


# --- org owner role (USR-06a) ------------------------------------------------


async def _org_app(db_session: AsyncSession, key: str = "calechip") -> tuple[App, Role]:
    app = await make_app(db_session, key, org_mode="required")
    return app, await make_role(db_session, app, f"{key}:org-owner")


async def test_patch_org_owner_role_happy(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, owner = await _org_app(db_session)
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(owner.id)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["org_owner_role_id"] == str(owner.id)
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": None})
    assert resp.status_code == 200
    assert resp.json()["org_owner_role_id"] is None


async def test_patch_org_owner_role_set_with_org_mode_in_same_request(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "calechip")
    owner = await make_role(db_session, app, "calechip:org-owner")
    resp = await rbac_client.patch(
        f"{BASE}/calechip", json={"org_mode": "required", "org_owner_role_id": str(owner.id)}
    )
    assert resp.status_code == 200, resp.text


async def test_patch_org_owner_role_unknown_role_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _org_app(db_session)
    resp = await rbac_client.patch(
        f"{BASE}/calechip", json={"org_owner_role_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == [
        {"field": "org_owner_role_id", "reason": "unknown_role"}
    ]


async def test_patch_org_owner_role_other_app_role_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _org_app(db_session)
    stoktok = await make_app(db_session, "stoktok")
    foreign = await make_role(db_session, stoktok, "stoktok:viewer")
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(foreign.id)})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "unknown_role"


async def test_patch_org_owner_role_admin_perm_role_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, owner = await _org_app(db_session)
    await grant(
        db_session,
        owner,
        await make_permission(db_session, app, "calechip:org:manage", is_admin=True),
    )
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(owner.id)})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "role_has_admin_permissions"


async def test_patch_org_owner_role_org_mode_none_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "calechip")
    owner = await make_role(db_session, app, "calechip:org-owner")
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(owner.id)})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "org_mode_required"


async def test_patch_org_owner_role_admin_app_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _app_row(db_session, "admin")
    assert admin is not None
    role = await make_role(db_session, admin, "admin:org-owner")
    resp = await rbac_client.patch(f"{BASE}/admin", json={"org_owner_role_id": str(role.id)})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "org_mode_required"


async def test_patch_org_owner_role_same_as_default_role_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _org_app(db_session)
    assert (
        await rbac_client.patch(f"{BASE}/calechip", json={"default_role_id": str(role.id)})
    ).status_code == 200
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(role.id)})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == [
        {"field": "org_owner_role_id", "reason": "same_as_default_role"}
    ]


async def test_patch_default_role_same_as_org_owner_role_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _org_app(db_session)
    assert (
        await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(role.id)})
    ).status_code == 200
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"default_role_id": str(role.id)})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "same_as_default_role"


async def test_patch_both_roles_the_same_in_one_request_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _org_app(db_session)
    resp = await rbac_client.patch(
        f"{BASE}/calechip",
        json={"default_role_id": str(role.id), "org_owner_role_id": str(role.id)},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "same_as_default_role"


async def test_patch_org_mode_none_while_owner_set_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, owner = await _org_app(db_session)
    assert (
        await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(owner.id)})
    ).status_code == 200
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_mode": "none"})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "org_mode_required"
    both = await rbac_client.patch(
        f"{BASE}/calechip", json={"org_mode": "none", "org_owner_role_id": None}
    )
    assert both.status_code == 200, both.text


async def _app_updated_rows(db_session: AsyncSession) -> list[AuditLog]:
    rows = await db_session.scalars(select(AuditLog).where(AuditLog.action == "app.updated"))
    return list(rows)


async def _add_entitlement(db_session: AsyncSession, app: App) -> None:
    org = await make_org(db_session)
    db_session.add(OrgEntitlement(org_id=org.id, app_id=app.id, plan="pro", enabled=False))
    await db_session.flush()


async def _assert_org_mode_in_use(
    rbac_client: AsyncClient, db_session: AsyncSession, app: App
) -> None:
    resp = await rbac_client.patch(f"{BASE}/{app.key}", json={"org_mode": "none"})
    assert resp.status_code == 409, resp.text
    err = resp.json()["error"]
    assert err["code"] == "conflict"
    assert err["details"] == [{"field": "org_mode", "reason": "org_mode_in_use"}]
    await db_session.refresh(app)
    assert app.org_mode == "required"
    assert await _app_updated_rows(db_session) == []


async def test_patch_org_mode_none_with_entitlement_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, _ = await _org_app(db_session)
    await _add_entitlement(db_session, app)
    await _assert_org_mode_in_use(rbac_client, db_session, app)


async def test_patch_org_mode_none_with_org_assignment_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _org_app(db_session)
    org = await make_org(db_session)
    await assign(db_session, await make_user(db_session), role, org_id=org.id)
    await _assert_org_mode_in_use(rbac_client, db_session, app)


async def test_patch_org_mode_none_with_expired_org_assignment_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _org_app(db_session)
    org = await make_org(db_session)
    past = datetime.now(UTC) - timedelta(days=1)
    await assign(db_session, await make_user(db_session), role, org_id=org.id, expires_at=past)
    await _assert_org_mode_in_use(rbac_client, db_session, app)


async def test_patch_org_mode_none_with_only_app_scope_assignments_ok(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _org_app(db_session)
    await assign(db_session, await make_user(db_session), role)
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_mode": "none"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["org_mode"] == "none"


async def test_patch_org_mode_none_without_org_data_ok(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _org_app(db_session)
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_mode": "none"})
    assert resp.status_code == 200, resp.text
    rows = await _app_updated_rows(db_session)
    assert len(rows) == 1
    assert rows[0].changes == {"org_mode": {"from": "required", "to": "none"}}


async def test_patch_org_mode_none_and_clear_owner_without_org_data_ok(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, owner = await _org_app(db_session)
    assert (
        await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(owner.id)})
    ).status_code == 200
    resp = await rbac_client.patch(
        f"{BASE}/calechip", json={"org_mode": "none", "org_owner_role_id": None}
    )
    assert resp.status_code == 200, resp.text


async def test_patch_org_mode_none_owner_set_and_org_data_422_wins(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, owner = await _org_app(db_session)
    assert (
        await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(owner.id)})
    ).status_code == 200
    await _add_entitlement(db_session, app)
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_mode": "none"})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "org_mode_required"
    both = await rbac_client.patch(
        f"{BASE}/calechip", json={"org_mode": "none", "org_owner_role_id": None}
    )
    assert both.status_code == 409
    assert both.json()["error"]["details"][0]["reason"] == "org_mode_in_use"
    await db_session.refresh(app)
    assert app.org_owner_role_id == owner.id
    assert app.org_mode == "required"


async def test_patch_org_mode_required_with_org_data_unaffected(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "calechip", org_mode="none")
    role = await make_role(db_session, app, "calechip:member")
    org = await make_org(db_session)
    await assign(db_session, await make_user(db_session), role, org_id=org.id)
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_mode": "required"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["org_mode"] == "required"


async def test_patch_org_mode_same_value_with_org_data_ok(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, _ = await _org_app(db_session)
    await _add_entitlement(db_session, app)
    same = await rbac_client.patch(f"{BASE}/calechip", json={"org_mode": "required"})
    assert same.status_code == 200, same.text
    assert await _app_updated_rows(db_session) == []
    other = await rbac_client.patch(f"{BASE}/calechip", json={"name": "Renamed"})
    assert other.status_code == 200, other.text


async def test_patch_org_owner_role_is_audited(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, owner = await _org_app(db_session)
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_owner_role_id": str(owner.id)})
    assert resp.status_code == 200
    row = (await db_session.scalars(select(AuditLog).where(AuditLog.action == "app.updated"))).one()
    assert row.changes == {"org_owner_role_id": {"from": None, "to": str(owner.id)}}


# --- org-level permissions never reach a default role (USR-06c, ADR-0015) --------


async def test_patch_default_role_with_org_permission_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, _ = await _org_app(db_session)
    role = await make_role(db_session, app, "calechip:member")
    await grant(
        db_session, role, await make_permission(db_session, app, "calechip:a:b", scope="org")
    )
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"default_role_id": str(role.id)})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "role_has_org_permissions"
    await db_session.refresh(app)
    assert app.default_role_id is None


async def test_patch_org_mode_required_default_holds_org_permission_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "calechip")
    role = await make_role(db_session, app, "calechip:member")
    await grant(
        db_session, role, await make_permission(db_session, app, "calechip:a:b", scope="org")
    )
    app.default_role_id = role.id
    await db_session.flush()
    resp = await rbac_client.patch(f"{BASE}/calechip", json={"org_mode": "required"})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["reason"] == "role_has_org_permissions"


# --- member_count (ADM-016, OQ5) -----------------------------------------------


async def _counts(c: AsyncClient) -> dict[str, int]:
    resp = await c.get(BASE, params={"limit": 100})
    assert resp.status_code == 200
    return {i["key"]: i["member_count"] for i in resp.json()["items"]}


async def _detail_count(c: AsyncClient, key: str) -> int:
    resp = await c.get(f"{BASE}/{key}")
    assert resp.status_code == 200
    return int(resp.json()["member_count"])


async def test_list_apps_member_count(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    one = await make_app(db_session, "ma-one")
    two = await make_app(db_session, "ma-two")
    await make_app(db_session, "ma-zero")
    r1, r2 = await make_role(db_session, one, "mm:r"), await make_role(db_session, two, "mm:r")
    await assign(db_session, await make_login_user(db_session), r1)
    for _ in range(2):
        await assign(db_session, await make_login_user(db_session), r2)
    counts = await _counts(rbac_client)
    assert (counts["ma-one"], counts["ma-two"], counts["ma-zero"]) == (1, 2, 0)
    total = (await rbac_client.get(BASE)).json()["total"]
    # Page scoped to ids: the page holding only `ma-zero` reports 0 (default-0 path).
    for off in range(total):
        page = (await rbac_client.get(BASE, params={"limit": 1, "offset": off})).json()["items"]
        if page[0]["key"] == "ma-zero":
            assert page[0]["member_count"] == 0
            break
    else:
        raise AssertionError("ma-zero not listed")


async def test_get_app_member_count(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, "ma-get")
    await make_app(db_session, "ma-get0")
    role = await make_role(db_session, app, "mm:r")
    for _ in range(2):
        await assign(db_session, await make_login_user(db_session), role)
    assert await _detail_count(rbac_client, "ma-get") == 2
    assert await _detail_count(rbac_client, "ma-get0") == 0


async def test_member_count_app_and_org_scope(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "ma-scope")
    r1, r2 = await make_role(db_session, app, "mm:a"), await make_role(db_session, app, "mm:b")
    o1, o2 = await make_org(db_session), await make_org(db_session)
    user = await make_login_user(db_session)
    await assign(db_session, user, r1)
    await assign(db_session, user, r2)
    await assign(db_session, user, r1, org_id=o1.id)
    await assign(db_session, user, r1, org_id=o2.id)
    assert (await _counts(rbac_client))["ma-scope"] == 1
    assert await _detail_count(rbac_client, "ma-scope") == 1
    await assign(db_session, await make_login_user(db_session), r1, org_id=o1.id)
    assert (await _counts(rbac_client))["ma-scope"] == 2
    assert await _detail_count(rbac_client, "ma-scope") == 2


async def test_member_count_excludes_expired(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "ma-exp")
    role = await make_role(db_session, app, "mm:r")
    past = datetime.now(UTC) - timedelta(days=1)
    future = datetime.now(UTC) + timedelta(days=1)
    await assign(db_session, await make_login_user(db_session), role, expires_at=past)
    assert await _detail_count(rbac_client, "ma-exp") == 0
    await assign(db_session, await make_login_user(db_session), role, expires_at=future)
    assert await _detail_count(rbac_client, "ma-exp") == 1
    assert (await _counts(rbac_client))["ma-exp"] == 1


async def test_member_count_disabled_excluded_unverified_counted(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "ma-status")
    role = await make_role(db_session, app, "mm:r")
    await assign(db_session, await make_login_user(db_session, status="disabled"), role)
    await assign(db_session, await make_login_user(db_session, verified=False), role)
    assert await _detail_count(rbac_client, "ma-status") == 1


async def test_member_count_admin_app_counts_staff(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    before = await _detail_count(rbac_client, "admin")
    await make_staff(db_session, "admin:apps:read")
    assert await _detail_count(rbac_client, "admin") == before + 1


async def test_member_count_after_create_and_patch(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    created = await rbac_client.post(BASE, json={"key": "ma-new", "name": "New"})
    assert created.status_code == 201
    assert created.json()["member_count"] == 0
    role = await db_session.get(Role, uuid.UUID(created.json()["default_role_id"]))
    assert role is not None
    await assign(db_session, await make_login_user(db_session), role)
    patched = await rbac_client.patch(f"{BASE}/ma-new", json={"name": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["member_count"] == 1


async def test_member_count_matches_dashboard(
    rbac_client: AsyncClient, db_session: AsyncSession, make_client: MakeClient
) -> None:
    mixed = await make_app(db_session, "ma-mix")
    await make_app(db_session, "ma-none")
    role = await make_role(db_session, mixed, "mm:r")
    org = await make_org(db_session)
    past = datetime.now(UTC) - timedelta(days=1)
    await assign(db_session, await make_login_user(db_session), role)
    await assign(db_session, await make_login_user(db_session), role, org_id=org.id)
    await assign(db_session, await make_login_user(db_session), role, expires_at=past)
    await assign(db_session, await make_login_user(db_session, status="disabled"), role)
    await assign(db_session, await make_login_user(db_session, verified=False), role)
    listed = await _counts(rbac_client)
    async with make_client(admin_permissions=["admin:*"]) as c:
        resp = await c.get("/api/v1/stats/dashboard")
    assert resp.status_code == 200
    dash = {r["app_key"]: r["member_count"] for r in resp.json()["members_per_app"]}
    assert listed == dash
    assert listed["ma-mix"] == 3


async def test_member_count_visible_with_apps_read_only(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, "ma-ro")
    await assign(
        db_session, await make_login_user(db_session), await make_role(db_session, app, "mm:r")
    )
    async with make_client(admin_permissions=["admin:apps:read"]) as c:
        assert (await _counts(c))["ma-ro"] == 1
        assert await _detail_count(c, "ma-ro") == 1


async def _app_statements(
    make_client: MakeClient, db_session: AsyncSession, path: str
) -> list[str]:
    engine = (await db_session.connection()).sync_connection.engine
    seen: list[str] = []

    def _listener(*args: Any) -> None:
        seen.append(args[2])

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        async with make_client(admin_permissions=["admin:apps:read"]) as c:
            assert (await c.get(path)).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _listener)
    return seen


async def _app_with_member(db_session: AsyncSession) -> App:
    app = await make_app(db_session, f"qa-{uuid.uuid4().hex[:8]}")
    await assign(
        db_session, await make_login_user(db_session), await make_role(db_session, app, "mm:r")
    )
    return app


async def test_list_apps_query_count(make_client: MakeClient, db_session: AsyncSession) -> None:
    await _app_with_member(db_session)
    few = await _app_statements(make_client, db_session, BASE)
    for _ in range(4):
        await _app_with_member(db_session)
    many = await _app_statements(make_client, db_session, BASE)
    assert len(few) == len(many) == 3
    assert sum("role_assignments" in s for s in many) == 1


async def test_get_app_query_count(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app_with_member(db_session)
    seen = await _app_statements(make_client, db_session, f"{BASE}/{app.key}")
    assert len(seen) == 2


# --- list filters and sort (USR-18) ---

T0 = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed_app(
    db_session: AsyncSession,
    key: str,
    name: str,
    *,
    status: str = "active",
    join_policy: str = "open",
    org_mode: str = "none",
    created_at: datetime = T0,
) -> App:
    app = App(
        key=key,
        name=name,
        status=status,
        join_policy=join_policy,
        org_mode=org_mode,
        created_at=created_at,
    )
    db_session.add(app)
    await db_session.flush()
    await db_session.refresh(app)
    return app


async def _keys(client: AsyncClient, query: str = "") -> list[str]:
    resp = await client.get(f"{BASE}?{query}")
    assert resp.status_code == 200
    return [i["key"] for i in resp.json()["items"]]


def test_apps_sort_allow_list_matches_repository_mapping() -> None:
    for value in get_args(AppSort):
        assert sort_clauses(value, _APP_SORT_COLUMNS, App.id)


async def test_list_apps_filter_status(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_app(db_session, "alpha", "Alpha")
    await _seed_app(db_session, "bravo", "Bravo", status="disabled")
    resp = await rbac_client.get(f"{BASE}?status=disabled")
    assert [i["key"] for i in resp.json()["items"]] == ["bravo"]
    assert resp.json()["total"] == 1
    resp = await rbac_client.get(f"{BASE}?status=active")
    assert [i["key"] for i in resp.json()["items"]] == ["admin", "alpha"]
    assert resp.json()["total"] == 2


async def test_list_apps_filter_join_policy(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_app(db_session, "po", "Po", join_policy="open")
    await _seed_app(db_session, "pa", "Pa", join_policy="approval")
    await _seed_app(db_session, "pc", "Pc", join_policy="closed")
    resp = await rbac_client.get(f"{BASE}?join_policy=approval")
    assert [i["key"] for i in resp.json()["items"]] == ["pa"]
    assert resp.json()["total"] == 1
    resp = await rbac_client.get(f"{BASE}?join_policy=closed")
    assert [i["key"] for i in resp.json()["items"]] == ["admin", "pc"]
    assert resp.json()["total"] == 2


async def test_list_apps_filter_org_mode(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_app(db_session, "mn", "Mn", org_mode="none")
    await _seed_app(db_session, "mr", "Mr", org_mode="required")
    resp = await rbac_client.get(f"{BASE}?org_mode=required")
    assert [i["key"] for i in resp.json()["items"]] == ["mr"]
    assert resp.json()["total"] == 1


async def test_list_apps_filter_q_key_or_name_case_insensitive(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_app(db_session, "one", "Alpha")
    await _seed_app(db_session, "bravo", "Other")
    await _seed_app(db_session, "three", "Charlie")
    assert await _keys(rbac_client, "q=ALP") == ["one"]
    assert await _keys(rbac_client, "q=BRAV") == ["bravo"]


async def test_list_apps_filter_q_is_trimmed(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_app(db_session, "one", "Alpha")
    resp = await rbac_client.get(BASE, params={"q": " alpha "})
    assert [i["key"] for i in resp.json()["items"]] == ["one"]


@pytest.mark.parametrize(
    ("q", "expected"),
    [("%", ["pure"]), ("a_b", ["ab"]), ("_", ["ab"]), ("\\", ["back"])],
)
async def test_list_apps_filter_q_escapes_like_wildcards(
    rbac_client: AsyncClient, db_session: AsyncSession, q: str, expected: list[str]
) -> None:
    await _seed_app(db_session, "pure", "100% Pure")
    await _seed_app(db_session, "plain", "100 Pure")
    await _seed_app(db_session, "ab", "a_b")
    await _seed_app(db_session, "axb", "axb")
    await _seed_app(db_session, "back", "back\\slash")
    resp = await rbac_client.get(BASE, params={"q": q})
    assert [i["key"] for i in resp.json()["items"]] == expected


async def test_list_apps_filters_combined(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_app(db_session, "hit", "Alpha", status="disabled", join_policy="approval")
    await _seed_app(db_session, "ahit", "Alpha", status="active", join_policy="approval")
    await _seed_app(db_session, "bhit", "Alpha", status="disabled", join_policy="open")
    await _seed_app(db_session, "chit", "Other", status="disabled", join_policy="approval")
    resp = await rbac_client.get(f"{BASE}?status=disabled&join_policy=approval&q=alph")
    assert [i["key"] for i in resp.json()["items"]] == ["hit"]
    assert resp.json()["total"] == 1


async def test_list_apps_total_is_filtered_count(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    for key in ("matcha", "matchb", "matchc", "othera", "otherb"):
        await _seed_app(db_session, key, key.title())
    resp = await rbac_client.get(f"{BASE}?q=match&limit=1")
    body = resp.json()
    assert (body["total"], len(body["items"]), body["limit"]) == (3, 1, 1)


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("key", ["ka", "kb", "kc"]),
        ("-key", ["kc", "kb", "ka"]),
        ("name", ["kc", "ka", "kb"]),
        ("-name", ["kb", "ka", "kc"]),
        ("created_at", ["kb", "kc", "ka"]),
        ("-created_at", ["ka", "kc", "kb"]),
    ],
)
async def test_list_apps_sort_orders(
    rbac_client: AsyncClient, db_session: AsyncSession, sort: str, expected: list[str]
) -> None:
    await _seed_app(db_session, "ka", "Bravo", created_at=T0 + timedelta(days=3))
    await _seed_app(db_session, "kb", "Charlie", created_at=T0 + timedelta(days=1))
    await _seed_app(db_session, "kc", "Alpha", created_at=T0 + timedelta(days=2))
    keys = [k for k in await _keys(rbac_client, f"sort={sort}") if k != "admin"]
    assert keys == expected


@pytest.mark.parametrize("sort", ["name", "-name", "created_at", "-created_at"])
async def test_list_apps_sort_tie_breaker_is_id_asc(
    rbac_client: AsyncClient, db_session: AsyncSession, sort: str
) -> None:
    t1 = await _seed_app(db_session, "tie1", "Same")
    t2 = await _seed_app(db_session, "tie2", "Same")
    await _seed_app(db_session, "other", "Other", created_at=T0 + timedelta(days=1))
    tied = {t1.key: t1.id, t2.key: t2.id}
    keys = [k for k in await _keys(rbac_client, f"sort={sort}") if k in tied]
    assert keys == sorted(tied, key=lambda k: tied[k])


async def test_list_apps_default_order_unchanged(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_app(db_session, "zed", "Aaa")
    await _seed_app(db_session, "bee", "Zzz")
    default = (await rbac_client.get(BASE)).json()["items"]
    assert default == (await rbac_client.get(f"{BASE}?sort=key")).json()["items"]
    keys = [i["key"] for i in default]
    assert keys == sorted(keys)


async def test_list_apps_member_count_on_filtered_page(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    mca = await _seed_app(db_session, "mca", "Mca")
    mcb = await _seed_app(db_session, "mcb", "Mcb")
    ra, rb = await make_role(db_session, mca, "mm:r"), await make_role(db_session, mcb, "mm:r")
    await assign(db_session, await make_login_user(db_session), ra)
    for _ in range(2):
        await assign(db_session, await make_login_user(db_session), rb)
    items = (await rbac_client.get(f"{BASE}?q=mcb")).json()["items"]
    assert [(i["key"], i["member_count"]) for i in items] == [("mcb", 2)]
    counts = {
        i["key"]: i["member_count"]
        for i in (await rbac_client.get(f"{BASE}?status=active&sort=-key")).json()["items"]
    }
    assert (counts["mca"], counts["mcb"]) == (1, 2)


async def test_list_apps_query_count_with_filters(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    path = f"{BASE}?status=active&q=qa-&sort=-name"
    await _app_with_member(db_session)
    few = await _app_statements(make_client, db_session, path)
    for _ in range(4):
        await _app_with_member(db_session)
    many = await _app_statements(make_client, db_session, path)
    assert len(few) == len(many) == 3
    assert sum("role_assignments" in s for s in many) == 1


@pytest.mark.parametrize(
    "query",
    [
        "status=deleted",
        "join_policy=x",
        "org_mode=x",
        "sort=bogus",
        "sort=id",
        "sort=KEY",
        "sort=member_count",
        "q=",
        "q=%20%20%20",
        "q=" + "x" * 101,
    ],
)
async def test_list_apps_rejects_bad_query(rbac_client: AsyncClient, query: str) -> None:
    resp = await rbac_client.get(f"{BASE}?{query}")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_list_apps_ignores_unknown_params(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_app(db_session, "alpha", "Alpha")
    plain = (await rbac_client.get(BASE)).json()["items"]
    resp = await rbac_client.get(f"{BASE}?foo=bar")
    assert resp.status_code == 200
    assert resp.json()["items"] == plain


async def test_list_apps_without_permission_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await make_staff(db_session)
    headers = bearer(mint(staff))
    for query in ("q=acme&sort=name", "sort=bogus"):
        resp = await client.get(f"{BASE}?{query}", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "permission_denied"
        assert resp.json()["error"]["details"] == {"required": "admin:apps:read"}
