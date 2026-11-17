import unicodedata
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
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
    "org_owner_role_id",
    "created_at",
    "updated_at",
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
