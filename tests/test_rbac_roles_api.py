import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, Permission, Role, RolePermission
from app.modules.rbac.repository import RbacRepository
from tests.conftest import MakeClient
from tests.rbac_factories import assign, grant, make_app, make_permission, make_role, make_user

BASE = "/api/v1/apps"
URL = f"{BASE}/stoktok/roles"
ROLE_READ_KEYS = {"id", "key", "name", "is_system", "permissions"}


@pytest.fixture
async def rbac_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_rbac_api_enabled=True) as c:
        yield c


@contextmanager
def _capture_sql(db_session: AsyncSession) -> Iterator[list[str]]:
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(conn, "before_cursor_execute", _record)


async def _held(db_session: AsyncSession, role_id: uuid.UUID) -> set[str]:
    return set((await RbacRepository(db_session).role_permission_keys(role_id)).values())


async def _role(db_session: AsyncSession, key: str) -> Role | None:
    role = await db_session.scalar(select(Role).where(Role.key == key))
    if role is not None:
        await db_session.refresh(role)
    return role


async def _setup(db_session: AsyncSession) -> tuple[App, Role]:
    """`stoktok` with permissions a, b, c, an admin one, and a non-default role `viewer`."""
    app = await make_app(db_session, "stoktok")
    for key in ("stoktok:a:read", "stoktok:b:read", "stoktok:c:read"):
        await make_permission(db_session, app, key)
    await make_permission(db_session, app, "stoktok:roles:manage", is_admin=True)
    role = await make_role(db_session, app, "stoktok:viewer")
    return app, role


async def _create_app_with_member(rbac_client: AsyncClient, db_session: AsyncSession) -> Role:
    """POST /apps builds `stoktok:member` (system) as the default role."""
    assert (await rbac_client.post(BASE, json={"key": "stoktok", "name": "S"})).status_code == 201
    role = await _role(db_session, "stoktok:member")
    assert role is not None
    return role


def _put_url(role_id: uuid.UUID, app: str = "stoktok") -> str:
    return f"{BASE}/{app}/roles/{role_id}/permissions"


# --- read shape / create -----------------------------------------------------


async def test_role_read_has_exact_keys(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    await _setup(db_session)
    created = await rbac_client.post(URL, json={"key": "stoktok:editor", "name": "Editor"})
    assert set(created.json()) == ROLE_READ_KEYS
    rid = created.json()["id"]
    patched = await rbac_client.patch(f"{URL}/{rid}", json={"name": "Ed"})
    assert set(patched.json()) == ROLE_READ_KEYS
    put = await rbac_client.put(
        f"{URL}/{rid}/permissions", json={"permission_keys": ["stoktok:a:read"]}
    )
    assert set(put.json()) == ROLE_READ_KEYS


async def test_create_role(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    await make_app(db_session, "stoktok")
    resp = await rbac_client.post(URL, json={"key": "stoktok:editor", "name": "Editor"})
    assert resp.status_code == 201
    body = resp.json()
    assert (body["key"], body["name"], body["is_system"], body["permissions"]) == (
        "stoktok:editor",
        "Editor",
        False,
        [],
    )
    role = await _role(db_session, "stoktok:editor")
    assert role is not None and str(role.id) == body["id"] and not role.is_system


async def test_create_role_prefix_mismatch_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_app(db_session, "stoktok")
    await make_app(db_session, "calechip")
    resp = await rbac_client.post(URL, json={"key": "calechip:viewer", "name": "V"})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "unprocessable"
    assert error["details"] == [{"field": "key", "reason": "prefix_mismatch"}]
    assert await _role(db_session, "calechip:viewer") is None


@pytest.mark.parametrize(
    "body",
    [
        {"key": "stoktok", "name": "x"},
        {"key": "stoktok:a:b", "name": "x"},
        {"key": "Stoktok:x", "name": "x"},
        {"key": "stoktok:x"},
        {"key": "stoktok:x", "name": ""},
        {"key": "stoktok:x", "name": "n" * 256},
        {"key": "stoktok:x", "name": "a\x07b"},
        {"key": "stoktok:x", "name": "x", "is_system": True},
        {"key": "stoktok:x", "name": "x", "id": str(uuid.uuid4())},
    ],
)
async def test_create_role_validation_422(
    rbac_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    await make_app(db_session, "stoktok")
    resp = await rbac_client.post(URL, json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert await _role(db_session, "stoktok:x") is None


async def test_create_role_duplicate_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_app_with_member(rbac_client, db_session)
    resp = await rbac_client.post(URL, json={"key": "stoktok:member", "name": "Again"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_create_role_unknown_app_404(rbac_client: AsyncClient) -> None:
    resp = await rbac_client.post(f"{BASE}/nope/roles", json={"key": "nope:x", "name": "X"})
    assert resp.status_code == 404


async def test_create_role_integrity_race_is_409(
    rbac_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _setup(db_session)

    async def _missing(self: RbacRepository, app_id: uuid.UUID, key: str) -> Role | None:
        return None

    monkeypatch.setattr(RbacRepository, "get_role_by_key", _missing)
    resp = await rbac_client.post(URL, json={"key": "stoktok:viewer", "name": "V"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    # The savepoint kept the session usable.
    assert await db_session.scalar(select(func.count()).select_from(App)) == 2


# --- patch -------------------------------------------------------------------


async def test_patch_role_name(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    member = await _create_app_with_member(rbac_client, db_session)
    viewer = (await rbac_client.post(URL, json={"key": "stoktok:viewer", "name": "V"})).json()

    resp = await rbac_client.patch(f"{URL}/{viewer['id']}", json={"name": "Viewer"})
    assert resp.status_code == 200 and resp.json()["name"] == "Viewer"
    resp = await rbac_client.patch(f"{URL}/{member.id}", json={"name": "Members"})
    assert resp.status_code == 200 and resp.json()["name"] == "Members"
    assert resp.json()["is_system"] is True

    before = resp.json()
    resp = await rbac_client.patch(f"{URL}/{member.id}", json={})
    assert resp.status_code == 200
    assert resp.json() == before


@pytest.mark.parametrize("body", [{"key": "stoktok:other"}, {"is_system": True}, {"name": None}])
async def test_patch_role_rejects_key_and_is_system(
    rbac_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    _, role = await _setup(db_session)
    resp = await rbac_client.patch(f"{URL}/{role.id}", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    refreshed = await _role(db_session, "stoktok:viewer")
    assert refreshed is not None and not refreshed.is_system and refreshed.name == "stoktok:viewer"


async def test_patch_role_404(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    other = await make_app(db_session, "calechip")
    foreign = await make_role(db_session, other, "calechip:viewer")
    body = {"name": "x"}
    assert (await rbac_client.patch(f"{BASE}/nope/roles/{role.id}", json=body)).status_code == 404
    assert (await rbac_client.patch(f"{URL}/{uuid.uuid4()}", json=body)).status_code == 404
    assert (await rbac_client.patch(f"{URL}/{foreign.id}", json=body)).status_code == 404


# --- delete ------------------------------------------------------------------


async def test_delete_role(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    perm = await db_session.scalar(select(Permission).where(Permission.key == "stoktok:a:read"))
    assert perm is not None
    await grant(db_session, role, perm)

    resp = await rbac_client.delete(f"{URL}/{role.id}")
    assert resp.status_code == 204
    assert await db_session.scalar(select(Role).where(Role.id == role.id)) is None
    links = (
        select(func.count()).select_from(RolePermission).where(RolePermission.role_id == role.id)
    )
    assert await db_session.scalar(links) == 0


async def test_delete_system_role_409(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    super_admin = await _role(db_session, "admin:super-admin")
    assert super_admin is not None
    resp = await rbac_client.delete(f"{BASE}/admin/roles/{super_admin.id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"

    member = await _create_app_with_member(rbac_client, db_session)
    patch = await rbac_client.patch(f"{BASE}/stoktok", json={"default_role_id": None})
    assert patch.status_code == 200
    resp = await rbac_client.delete(f"{URL}/{member.id}")
    assert resp.status_code == 409
    assert await _role(db_session, "admin:super-admin") is not None
    assert await _role(db_session, "stoktok:member") is not None


async def test_delete_default_role_409(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    patch = await rbac_client.patch(f"{BASE}/stoktok", json={"default_role_id": str(role.id)})
    assert patch.status_code == 200
    resp = await rbac_client.delete(f"{URL}/{role.id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert await _role(db_session, "stoktok:viewer") is not None


async def test_delete_role_with_assignments_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    await assign(db_session, await make_user(db_session), role)
    resp = await rbac_client.delete(f"{URL}/{role.id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert await _role(db_session, "stoktok:viewer") is not None


async def test_delete_role_404(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    other = await make_app(db_session, "calechip")
    foreign = await make_role(db_session, other, "calechip:viewer")
    assert (await rbac_client.delete(f"{BASE}/nope/roles/{role.id}")).status_code == 404
    assert (await rbac_client.delete(f"{URL}/{uuid.uuid4()}")).status_code == 404
    assert (await rbac_client.delete(f"{URL}/{foreign.id}")).status_code == 404
    assert await _role(db_session, "calechip:viewer") is not None


# --- PUT permissions ---------------------------------------------------------


async def test_put_role_permissions_replaces_set(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    await rbac_client.put(
        _put_url(role.id), json={"permission_keys": ["stoktok:a:read", "stoktok:b:read"]}
    )
    resp = await rbac_client.put(
        _put_url(role.id), json={"permission_keys": ["stoktok:c:read", "stoktok:b:read"]}
    )
    assert resp.status_code == 200
    assert resp.json()["permissions"] == ["stoktok:b:read", "stoktok:c:read"]
    assert await _held(db_session, role.id) == {"stoktok:b:read", "stoktok:c:read"}


async def test_put_role_permissions_dedupes_and_is_idempotent(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    body = {"permission_keys": ["stoktok:c:read", "stoktok:a:read", "stoktok:a:read"]}
    first = await rbac_client.put(_put_url(role.id), json=body)
    assert first.status_code == 200
    assert first.json()["permissions"] == ["stoktok:a:read", "stoktok:c:read"]

    with _capture_sql(db_session) as statements:
        second = await rbac_client.put(_put_url(role.id), json=body)
    assert second.status_code == 200
    assert second.json() == first.json()
    assert any("FOR NO KEY UPDATE" in s for s in statements)  # the capture saw the request
    writes = [
        s for s in statements if s.lstrip().upper().startswith(("INSERT", "DELETE", "UPDATE"))
    ]
    assert writes == []


async def test_put_role_permissions_empty_list(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    await rbac_client.put(_put_url(role.id), json={"permission_keys": ["stoktok:a:read"]})
    resp = await rbac_client.put(_put_url(role.id), json={"permission_keys": []})
    assert resp.status_code == 200
    assert resp.json()["permissions"] == []
    assert await _held(db_session, role.id) == set()


async def test_put_role_permissions_unknown_keys_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    other = await make_app(db_session, "calechip")
    await make_permission(db_session, other, "calechip:x:read")
    await rbac_client.put(_put_url(role.id), json={"permission_keys": ["stoktok:b:read"]})

    resp = await rbac_client.put(
        _put_url(role.id),
        json={
            "permission_keys": [
                "stoktok:a:read",
                "stoktok:zz:y",
                "stoktok:yy:y",
                "calechip:x:read",
            ]
        },
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "unprocessable"
    assert error["details"] == [
        {"field": "permission_keys", "reason": "unknown_permission", "key": k}
        for k in ("calechip:x:read", "stoktok:yy:y", "stoktok:zz:y")
    ]
    assert await _held(db_session, role.id) == {"stoktok:b:read"}


@pytest.mark.parametrize(
    "body",
    [
        {"permission_keys": ["*"]},
        {"permission_keys": ["stoktok:x:*"]},
        {"permission_keys": [f"stoktok:p{i}:read" for i in range(201)]},
        {"permission_keys": "x"},
        {},
    ],
)
async def test_put_role_permissions_format_422(
    rbac_client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    _, role = await _setup(db_session)
    resp = await rbac_client.put(_put_url(role.id), json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_put_role_permissions_404(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _setup(db_session)
    other = await make_app(db_session, "calechip")
    foreign = await make_role(db_session, other, "calechip:viewer")
    body = {"permission_keys": []}
    assert (await rbac_client.put(_put_url(role.id, "nope"), json=body)).status_code == 404
    assert (await rbac_client.put(_put_url(uuid.uuid4()), json=body)).status_code == 404
    assert (await rbac_client.put(_put_url(foreign.id), json=body)).status_code == 404


async def test_put_super_admin_without_wildcard_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    super_admin = await _role(db_session, "admin:super-admin")
    assert super_admin is not None
    before = await _held(db_session, super_admin.id)
    assert "admin:*" in before

    resp = await rbac_client.put(_put_url(super_admin.id, "admin"), json={"permission_keys": []})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert await _held(db_session, super_admin.id) == before

    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    await make_permission(db_session, admin, "admin:apps:read", is_admin=True)
    resp = await rbac_client.put(
        _put_url(super_admin.id, "admin"),
        json={"permission_keys": ["admin:*", "admin:apps:read"]},
    )
    assert resp.status_code == 200
    assert resp.json()["permissions"] == ["admin:*", "admin:apps:read"]


async def test_put_admin_permission_on_default_role_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    member = await _create_app_with_member(rbac_client, db_session)
    perms = f"{BASE}/stoktok/permissions"
    assert (await rbac_client.post(perms, json={"key": "stoktok:*"})).status_code == 201
    body = {"key": "stoktok:roles:manage", "is_admin": True}
    assert (await rbac_client.post(perms, json=body)).status_code == 201
    assert (await rbac_client.post(perms, json={"key": "stoktok:a:read"})).status_code == 201

    for key in ("stoktok:*", "stoktok:roles:manage"):
        resp = await rbac_client.put(
            _put_url(member.id), json={"permission_keys": ["stoktok:a:read", key]}
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "conflict"
        assert await _held(db_session, member.id) == set()

    resp = await rbac_client.put(_put_url(member.id), json={"permission_keys": ["stoktok:a:read"]})
    assert resp.status_code == 200
    assert resp.json()["permissions"] == ["stoktok:a:read"]


async def test_put_system_member_role_allowed(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    member = await _create_app_with_member(rbac_client, db_session)
    perms = f"{BASE}/stoktok/permissions"
    for key in ("stoktok:a:read", "stoktok:b:read"):
        assert (await rbac_client.post(perms, json={"key": key})).status_code == 201
    resp = await rbac_client.put(
        _put_url(member.id), json={"permission_keys": ["stoktok:b:read", "stoktok:a:read"]}
    )
    assert resp.status_code == 200
    assert resp.json()["is_system"] is True
    assert resp.json()["permissions"] == ["stoktok:a:read", "stoktok:b:read"]


async def test_default_then_put_admin_permission_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _setup(db_session)
    patch = await rbac_client.patch(f"{BASE}/stoktok", json={"default_role_id": str(role.id)})
    assert patch.status_code == 200
    resp = await rbac_client.put(
        _put_url(role.id), json={"permission_keys": ["stoktok:roles:manage"]}
    )
    assert resp.status_code == 409
    assert await _held(db_session, role.id) == set()


async def test_put_admin_permission_then_set_default_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _setup(db_session)
    resp = await rbac_client.put(
        _put_url(role.id), json={"permission_keys": ["stoktok:roles:manage"]}
    )
    assert resp.status_code == 200
    patch = await rbac_client.patch(f"{BASE}/stoktok", json={"default_role_id": str(role.id)})
    assert patch.status_code == 422
    assert patch.json()["error"]["code"] == "unprocessable"
    await db_session.refresh(app)
    assert app.default_role_id is None


async def test_put_role_permissions_integrity_race_is_409(
    rbac_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, role = await _setup(db_session)
    # "Deleted by a concurrent request": the PUT read it, the database no longer has it.
    ghost = Permission(id=uuid.uuid4(), app_id=app.id, key="stoktok:a:read", is_admin=False)

    async def _ghost(self: RbacRepository, app_id: uuid.UUID, keys: list[str]) -> list[Permission]:
        return [ghost]

    monkeypatch.setattr(RbacRepository, "permissions_by_keys", _ghost)
    resp = await rbac_client.put(_put_url(role.id), json={"permission_keys": ["stoktok:a:read"]})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    # The savepoint kept the session usable.
    assert await db_session.scalar(select(func.count()).select_from(App)) == 2
    assert await _held(db_session, role.id) == set()
