"""PUT /api/v1/apps/{key}/manifest: sync behaviour (USR-05b, ADR-0016 §6, ADR-0015)."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.app_credentials import codec
from app.modules.audit.models import AuditLog
from app.modules.rbac.actor import SYSTEM_CLI
from app.modules.rbac.models import App, Permission, Role, RolePermission
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.schemas import PermissionCreate
from app.modules.rbac.service import PermissionService
from tests.app_credential_factories import make_credential
from tests.rbac_factories import grant, make_app, make_permission, make_role

V1 = "/api/v1"
Body = dict[str, Any]


@dataclass
class Env:
    app: App
    key: str
    key_id: str
    headers: dict[str, str]

    def perm(self, name: str) -> str:
        return f"{self.key}:{name}:read"


async def _env(db: AsyncSession, *, org: bool = False) -> Env:
    key = f"app-{uuid.uuid4().hex[:8]}"
    app = await make_app(db, key, org_mode="required" if org else "none")
    row, raw = await make_credential(db, app)
    return Env(app, key, row.key_id, {"Authorization": f"Bearer {raw}"})


async def _put(client: AsyncClient, env: Env, body: Body) -> Response:
    return await client.put(f"{V1}/apps/{env.key}/manifest", json=body, headers=env.headers)


def _perms(env: Env, *names: str, **extra: Any) -> list[Body]:
    return [{"key": env.perm(n), **extra} for n in names]


def _reasons(resp: Response) -> set[str]:
    return {d["reason"] for d in resp.json()["error"]["details"]}


async def _holds(db: AsyncSession, env: Env, role_key: str) -> set[str]:
    repo = RbacRepository(db)
    role = await repo.get_role_by_key(env.app.id, role_key)
    assert role is not None
    return set((await repo.role_permission_keys(role.id)).values())


async def _stored(db: AsyncSession, env: Env) -> list[Permission]:
    rows = await db.scalars(
        select(Permission)
        .where(Permission.app_id == env.app.id)
        .order_by(Permission.key)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _audit(db: AsyncSession, env: Env) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.action == "app.manifest_synced", AuditLog.app_key == env.key)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _catalog_etag(client: AsyncClient, env: Env) -> str:
    resp = await client.get(f"{V1}/apps/{env.key}/roles")
    assert resp.status_code == 200
    return resp.headers["etag"]


# --- validation --------------------------------------------------------------


async def test_namespace_422_permissions(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await _put(client, env, {"permissions": [{"key": "other:x:read"}]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unprocessable"
    assert "prefix_mismatch" in _reasons(resp)
    assert await _stored(db_session, env) == []


async def test_namespace_422_roles(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    perms = _perms(env, "x")
    role_key = await _put(
        client,
        env,
        {"permissions": perms, "roles": [{"key": "other:r", "name": "R", "permissions": []}]},
    )
    assert role_key.status_code == 422
    assert "prefix_mismatch" in _reasons(role_key)
    role_perm = await _put(
        client,
        env,
        {
            "permissions": perms,
            "roles": [{"key": f"{env.key}:r", "name": "R", "permissions": ["other:x:read"]}],
        },
    )
    assert role_perm.status_code == 422
    assert "prefix_mismatch" in _reasons(role_perm)
    assert await _stored(db_session, env) == []


async def test_namespace_422_member(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    body = {"permissions": _perms(env, "x"), "member": {"permissions": ["other:x:read"]}}
    resp = await _put(client, env, body)
    assert resp.status_code == 422
    assert "prefix_mismatch" in _reasons(resp)
    assert await _stored(db_session, env) == []


def _cases() -> dict[str, tuple[Callable[[Env], Body], str]]:
    def role(env: Env, perms: list[str]) -> list[Body]:
        return [{"key": f"{env.key}:r", "name": "R", "permissions": perms}]

    return {
        "wildcard_not_allowed": (
            lambda e: {"permissions": [{"key": f"{e.key}:*"}]},
            "wildcard_not_allowed",
        ),
        "duplicate_key": (lambda e: {"permissions": _perms(e, "x", "x")}, "duplicate_key"),
        "duplicate_role": (
            lambda e: {
                "permissions": _perms(e, "x"),
                "roles": role(e, []) + role(e, []),
            },
            "duplicate_key",
        ),
        "reserved_scope": (
            lambda e: {"permissions": [{"key": f"{e.key}:org:create", "scope": "org"}]},
            "reserved_scope",
        ),
        "org_mode_required": (
            lambda e: {"permissions": _perms(e, "x", scope="org")},
            "org_mode_required",
        ),
        "undeclared_permission": (
            lambda e: {"permissions": _perms(e, "x"), "roles": role(e, [e.perm("y")])},
            "undeclared_permission",
        ),
        "admin_permission_in_role": (
            lambda e: {
                "permissions": _perms(e, "x", is_admin=True),
                "roles": role(e, [e.perm("x")]),
            },
            "admin_permission_in_role",
        ),
        "ineligible_member_permission": (
            lambda e: {
                "permissions": _perms(e, "x", is_admin=True),
                "member": {"permissions": [e.perm("x")]},
            },
            "ineligible_member_permission",
        ),
        "reserved_role": (
            lambda e: {
                "permissions": _perms(e, "x"),
                "roles": [{"key": f"{e.key}:member", "name": "M", "permissions": []}],
            },
            "reserved_role",
        ),
    }


@pytest.mark.parametrize("case", list(_cases()))
async def test_validation_reasons(client: AsyncClient, db_session: AsyncSession, case: str) -> None:
    env = await _env(db_session)
    build, reason = _cases()[case]
    resp = await _put(client, env, build(env))
    assert resp.status_code == 422, resp.text
    assert reason in _reasons(resp)
    assert await _stored(db_session, env) == []
    assert await _audit(db_session, env) == []


async def test_extra_field_is_validation_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    resp = await _put(client, env, {"permissions": [], "surprise": 1})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_org_app_scope_required_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session, org=True)
    resp = await _put(client, env, {"permissions": _perms(env, "x")})
    assert resp.status_code == 422
    assert "scope_required" in _reasons(resp)
    ok = await _put(client, env, {"permissions": [{"key": f"{env.key}:org:invite"}]})
    assert ok.status_code == 200, ok.text
    (stored,) = await _stored(db_session, env)
    assert stored.scope == "org"


async def test_limits_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    many = [{"key": f"{env.key}:r{i}:read"} for i in range(201)]
    resp = await _put(client, env, {"permissions": many})
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "validation_error")
    roles = [{"key": f"{env.key}:r{i}", "name": "R", "permissions": []} for i in range(51)]
    resp = await _put(client, env, {"permissions": [], "roles": roles})
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "validation_error")


# --- sync --------------------------------------------------------------------


async def test_new_permission_reaches_member(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await _put(client, env, {"permissions": _perms(env, "x")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed"] is True
    assert body["permissions"]["added"] == [env.perm("x")]
    assert body["member"] == {
        "role_key": f"{env.key}:member",
        "rebuilt": True,
        "added": [env.perm("x")],
        "removed": [],
    }
    assert await _holds(db_session, env, f"{env.key}:member") == {env.perm("x")}


async def test_is_admin_excluded_from_member(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    body = {"permissions": [*_perms(env, "x"), *_perms(env, "y", is_admin=True)]}
    resp = await _put(client, env, body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["member"]["added"] == [env.perm("x")]
    assert resp.json()["permissions"]["added"] == [env.perm("x"), env.perm("y")]
    assert await _holds(db_session, env, f"{env.key}:member") == {env.perm("x")}
    stored = {p.key: p for p in await _stored(db_session, env)}
    assert stored[env.perm("y")].is_admin is True


async def test_push_twice_is_idempotent(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    body = {"permissions": _perms(env, "x", "y")}
    assert (await _put(client, env, body)).json()["changed"] is True
    etag = await _catalog_etag(client, env)
    again = await _put(client, env, body)
    assert again.status_code == 200
    assert again.json() == {
        "app": env.key,
        "changed": False,
        "permissions": {"added": [], "description_updated": [], "not_in_manifest": []},
        "roles": {"added": [], "updated": []},
        "member": {"role_key": f"{env.key}:member", "rebuilt": False, "added": [], "removed": []},
    }
    assert len(await _audit(db_session, env)) == 1
    assert await _catalog_etag(client, env) == etag


async def test_manifest_change_bumps_role_version(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    editor = f"{env.key}:editor"

    def body(*names: str) -> Body:
        return {
            "permissions": _perms(env, "a", "b"),
            "roles": [
                {"key": editor, "name": "Editor", "permissions": [env.perm(n) for n in names]}
            ],
        }

    async def version() -> object:
        role = await RbacRepository(db_session).get_role_by_key(env.app.id, editor)
        assert role is not None
        await db_session.refresh(role)
        return role.updated_at

    assert (await _put(client, env, body("a"))).status_code == 200
    first = await version()
    assert (await _put(client, env, body("a"))).status_code == 200
    assert await version() == first
    assert (await _put(client, env, body("a", "b"))).status_code == 200
    assert await version() > first  # type: ignore[operator]


async def test_org_app_push_twice_never_adds_org_scope_to_member(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session, org=True)
    body = {
        "permissions": [
            {"key": f"{env.key}:org:create"},
            {"key": f"{env.key}:org:invite"},
            {"key": env.perm("team"), "scope": "org"},
        ]
    }
    first = await _put(client, env, body)
    assert first.status_code == 200, first.text
    second = await _put(client, env, body)
    assert second.json()["changed"] is False
    assert await _holds(db_session, env, f"{env.key}:member") == {f"{env.key}:org:create"}


async def test_requires_staff_409(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session, org=True)
    body = {"permissions": _perms(env, "x", scope="app")}
    resp = await _put(client, env, body)
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == [
        {"field": "permissions", "reason": "requires_staff", "key": env.perm("x")}
    ]
    assert await _stored(db_session, env) == []
    await PermissionService(db_session)._create(
        env.key, PermissionCreate(key=env.perm("x"), scope="app"), actor=SYSTEM_CLI
    )
    assert (await _put(client, env, body)).status_code == 200


async def test_flip_is_409_is_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    await make_permission(db_session, env.app, env.perm("x"), is_admin=False)
    resp = await _put(client, env, {"permissions": _perms(env, "x", is_admin=True)})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "is_admin_mismatch"
    (stored,) = await _stored(db_session, env)
    assert stored.is_admin is False


async def test_flip_is_409_scope(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session, org=True)
    await make_permission(db_session, env.app, env.perm("x"), scope="org")
    resp = await _put(client, env, {"permissions": _perms(env, "x", scope="app")})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "scope_mismatch"
    (stored,) = await _stored(db_session, env)
    assert stored.scope == "org"


async def test_override_narrows_never_widens(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    body = {"permissions": _perms(env, "a", "b"), "member": {"permissions": [env.perm("a")]}}
    resp = await _put(client, env, body)
    assert resp.status_code == 200, resp.text
    assert await _holds(db_session, env, f"{env.key}:member") == {env.perm("a")}
    assert (await _put(client, env, body)).json()["changed"] is False
    admin = {
        "permissions": _perms(env, "c", is_admin=True),
        "member": {"permissions": [env.perm("c")]},
    }
    assert (await _put(client, env, admin)).status_code == 422
    assert await _holds(db_session, env, f"{env.key}:member") == {env.perm("a")}


async def test_rebuild_readds_staff_removed_only_when_creating(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    assert (await _put(client, env, {"permissions": _perms(env, "a")})).status_code == 200
    member = await RbacRepository(db_session).get_role_by_key(env.app.id, f"{env.key}:member")
    assert member is not None
    await db_session.execute(delete(RolePermission).where(RolePermission.role_id == member.id))
    resp = await _put(client, env, {"permissions": _perms(env, "a")})
    assert resp.json()["changed"] is False
    assert await _holds(db_session, env, f"{env.key}:member") == set()
    resp = await _put(client, env, {"permissions": _perms(env, "a", "b")})
    assert resp.json()["member"]["added"] == [env.perm("a"), env.perm("b")]
    assert await _holds(db_session, env, f"{env.key}:member") == {env.perm("a"), env.perm("b")}


async def test_extra_roles_created_and_additive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    editor = f"{env.key}:editor"
    first = {
        "permissions": _perms(env, "a", "b"),
        "roles": [{"key": editor, "name": "Editor", "permissions": [env.perm("a")]}],
    }
    resp = await _put(client, env, first)
    assert resp.status_code == 200, resp.text
    assert resp.json()["roles"] == {"added": [editor], "updated": []}
    role = await RbacRepository(db_session).get_role_by_key(env.app.id, editor)
    assert role is not None and role.is_system is False
    by_hand = await make_permission(db_session, env.app, env.perm("z"))
    await grant(db_session, role, by_hand)
    second = {
        "permissions": _perms(env, "a", "b"),
        "roles": [
            {"key": editor, "name": "Renamed", "permissions": [env.perm("a"), env.perm("b")]}
        ],
    }
    resp = await _put(client, env, second)
    assert resp.json()["roles"] == {"added": [], "updated": [editor]}
    assert await _holds(db_session, env, editor) == {env.perm(n) for n in "abz"}
    await db_session.refresh(role)
    assert role.name == "Editor"


@pytest.mark.parametrize("kind", ["system", "default", "owner"])
async def test_role_not_manifest_managed_409(
    client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    env = await _env(db_session, org=kind == "owner")
    role = Role(app_id=env.app.id, key=f"{env.key}:special", name="S", is_system=kind == "system")
    db_session.add(role)
    await db_session.flush()
    if kind == "default":
        env.app.default_role_id = role.id
    if kind == "owner":
        env.app.org_owner_role_id = role.id
    await db_session.flush()
    scope = {"scope": "org"} if kind == "owner" else {}
    body = {
        "permissions": _perms(env, "a", **scope),
        "roles": [{"key": role.key, "name": "S", "permissions": [env.perm("a")]}],
    }
    resp = await _put(client, env, body)
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "role_not_manifest_managed"
    assert await _stored(db_session, env) == []


async def test_member_is_org_owner_409(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session, org=True)
    member = await make_role(db_session, env.app, f"{env.key}:member")
    env.app.org_owner_role_id = member.id
    await db_session.flush()
    resp = await _put(client, env, {"permissions": [{"key": f"{env.key}:org:create"}]})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "member_is_org_owner"
    assert await _stored(db_session, env) == []


async def test_not_in_manifest_reported_and_still_held(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    old = await make_permission(db_session, env.app, env.perm("old"))
    member = await make_role(db_session, env.app, f"{env.key}:member")
    editor = await make_role(db_session, env.app, f"{env.key}:editor")
    await grant(db_session, member, old)
    await grant(db_session, editor, old)
    resp = await _put(client, env, {"permissions": _perms(env, "new")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["permissions"]["not_in_manifest"] == [
        {"key": env.perm("old"), "held_by_roles": [editor.key, member.key]}
    ]
    assert env.perm("old") in {p.key for p in await _stored(db_session, env)}
    assert env.perm("old") in await _holds(db_session, env, member.key)
    assert await _holds(db_session, env, editor.key) == {env.perm("old")}


async def test_description_updated(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)

    def body(**extra: Any) -> Body:
        return {"permissions": _perms(env, "x", **extra)}

    await _put(client, env, body(description="one"))
    resp = await _put(client, env, body(description="two"))
    assert resp.json()["permissions"]["description_updated"] == [env.perm("x")]
    assert resp.json()["changed"] is True
    for extra in ({}, {"description": None}):
        resp = await _put(client, env, body(**extra))
        assert resp.json()["changed"] is False
    (stored,) = await _stored(db_session, env)
    assert stored.description == "two"


async def test_never_touches_app_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)

    async def snapshot() -> tuple[Any, ...]:
        app = await db_session.scalar(
            select(App).where(App.id == env.app.id).execution_options(populate_existing=True)
        )
        assert app is not None
        return (
            app.name,
            app.status,
            list(app.origins),
            app.default_role_id,
            app.org_owner_role_id,
            app.updated_at,
        )

    before = await snapshot()
    resp = await _put(client, env, {"permissions": _perms(env, "x")})
    assert resp.json()["changed"] is True
    assert await snapshot() == before


# --- role catalog ETag -------------------------------------------------------


async def test_etag_changes_on_member_reaching_addition(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    await _put(client, env, {"permissions": _perms(env, "a")})
    before = await _catalog_etag(client, env)
    await _put(client, env, {"permissions": _perms(env, "a", "b")})
    assert await _catalog_etag(client, env) != before


async def test_etag_changes_on_new_role(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    body: Body = {"permissions": _perms(env, "a")}
    await _put(client, env, body)
    before = await _catalog_etag(client, env)
    body["roles"] = [{"key": f"{env.key}:editor", "name": "Editor", "permissions": []}]
    await _put(client, env, body)
    assert await _catalog_etag(client, env) != before


async def test_etag_unchanged_on_repush_admin_only_and_description(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    body: Body = {"permissions": _perms(env, "a", description="one")}
    await _put(client, env, body)
    etag = await _catalog_etag(client, env)
    await _put(client, env, body)
    cached = await client.get(f"{V1}/apps/{env.key}/roles", headers={"If-None-Match": etag})
    assert cached.status_code == 304
    await _put(client, env, {"permissions": [*_perms(env, "a"), *_perms(env, "z", is_admin=True)]})
    assert await _catalog_etag(client, env) == etag
    await _put(client, env, {"permissions": _perms(env, "a", description="two")})
    assert await _catalog_etag(client, env) == etag


# --- audit -------------------------------------------------------------------


def _assert_secret_not_in(env: Env, raw: str, dump: str) -> None:
    prefix = f"mmak_{env.key_id}_"
    assert raw.startswith(prefix)
    secret = raw.removeprefix(prefix)
    assert len(secret) == 43  # token_urlsafe(32); may itself contain "_"
    assert raw not in dump
    assert secret not in dump


async def _assert_push_audited_without_secret(
    client: AsyncClient, db_session: AsyncSession, env: Env
) -> None:
    body = {
        "permissions": _perms(env, "a", "b", description="d"),
        "roles": [{"key": f"{env.key}:editor", "name": "E", "permissions": [env.perm("a")]}],
    }
    resp = await _put(client, env, body)
    assert resp.status_code == 200, resp.text
    (row,) = await _audit(db_session, env)
    assert (row.outcome, row.actor_kind) == ("success", "system")
    assert row.actor_label == f"app:{env.key}/{env.key_id}"
    assert (row.target_type, row.target_id) == ("app", env.app.id)
    assert row.changes is not None
    assert set(row.changes) <= {
        "permissions_added",
        "descriptions_updated",
        "roles_added",
        "roles_updated",
        "member_permissions",
    }
    assert set(row.changes) >= {"permissions_added", "roles_added", "member_permissions"}
    raw = env.headers["Authorization"].removeprefix("Bearer ")
    dump = " ".join(str(getattr(row, c.name)) for c in AuditLog.__table__.columns)
    _assert_secret_not_in(env, raw, dump)


async def test_changing_push_writes_one_row(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    await _assert_push_audited_without_secret(client, db_session, env)


async def test_audit_secret_check_handles_underscore_in_secret(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG-002: a secret containing "_" must be checked in full, not just its tail."""
    secret = "A" * 35 + "_success"
    monkeypatch.setattr(codec.secrets, "token_urlsafe", lambda _n: secret)
    env = await _env(db_session)
    monkeypatch.undo()
    assert env.headers["Authorization"].endswith("_success")
    await _assert_push_audited_without_secret(client, db_session, env)


async def test_noop_writes_no_row(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    body = {"permissions": _perms(env, "a")}
    await _put(client, env, body)
    before = len(await _audit(db_session, env))
    await _put(client, env, body)
    assert len(await _audit(db_session, env)) == before == 1


async def _designated(db: AsyncSession, env: Env) -> Role:
    role = await make_role(db, env.app, f"{env.key}:super-admin")
    env.app.super_admin_role_id = role.id
    await db.flush()
    return role


async def test_manifest_designated_role_409(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session, org=True)
    role = await _designated(db_session, env)
    body = {
        "permissions": _perms(env, "a", scope="app"),
        "roles": [{"key": role.key, "name": "X", "permissions": [env.perm("a")]}],
    }
    resp = await _put(client, env, body)
    assert resp.status_code == 409
    assert "role_not_manifest_managed" in _reasons(resp)
    assert await _holds(db_session, env, role.key) == set()


async def test_manifest_super_admin_role_id_field_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session, org=True)
    role = await _designated(db_session, env)
    body = {"permissions": _perms(env, "a", scope="app"), "super_admin_role_id": str(role.id)}
    assert (await _put(client, env, body)).status_code == 422


async def test_manifest_new_permission_does_not_touch_designated_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session, org=True)
    role = await _designated(db_session, env)
    resp = await _put(client, env, {"permissions": _perms(env, "x", scope="org")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["member"]["rebuilt"] is True
    assert await _holds(db_session, env, role.key) == set()


async def test_manifest_never_changes_designation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session, org=True)
    role = await _designated(db_session, env)
    assert (
        await _put(client, env, {"permissions": _perms(env, "x", "y", scope="org")})
    ).status_code == 200
    await db_session.refresh(env.app)
    assert env.app.super_admin_role_id == role.id
