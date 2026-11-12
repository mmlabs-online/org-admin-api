import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.main import create_app
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Role
from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_app, make_role, make_user, super_admin_role

ASSIGNMENT_KEYS = {
    "id",
    "app",
    "role_id",
    "role_key",
    "org_id",
    "granted_by",
    "expires_at",
    "active",
    "created_at",
}


@pytest.fixture
async def rbac_client(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_rbac_api_enabled=True) as c:
        yield c


def _url(user_id: uuid.UUID, assignment_id: uuid.UUID | str | None = None) -> str:
    base = f"/api/v1/users/{user_id}/role-assignments"
    return base if assignment_id is None else f"{base}/{assignment_id}"


def _future(days: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def _past(days: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _assert_error(resp: Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    error: dict[str, Any] = resp.json()["error"]
    assert error["code"] == code
    return error


def _assert_unprocessable(resp: Response, field: str, reason: str) -> None:
    error = _assert_error(resp, 422, "unprocessable")
    assert error["details"] == [{"field": field, "reason": reason}]


async def _stoktok(db_session: AsyncSession, **kwargs: object) -> tuple[App, Role]:
    app = await make_app(db_session, "stoktok", **kwargs)
    role = await make_role(db_session, app, "stoktok:viewer")
    return app, role


async def _make_org(db_session: AsyncSession) -> Organization:
    org = Organization(slug=f"org-{uuid.uuid4().hex[:8]}", name="Org")
    db_session.add(org)
    await db_session.flush()
    return org


# --- exposure ----------------------------------------------------------------


async def test_flag_off_404(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, role = await _stoktok(db_session)
    assert (await client.get(_url(user.id))).status_code == 404
    assert (await client.post(_url(user.id), json={"role_id": str(role.id)})).status_code == 404
    assert (await client.delete(_url(user.id, uuid.uuid4()))).status_code == 404


def test_route_permission_map() -> None:
    app = create_app(Settings(admin_rbac_api_enabled=True))
    expected = {
        ("GET", "/api/v1/users/{user_id}/role-assignments"): "admin:users:read",
        ("POST", "/api/v1/users/{user_id}/role-assignments"): "admin:roles:assign",
        (
            "DELETE",
            "/api/v1/users/{user_id}/role-assignments/{assignment_id}",
        ): "admin:roles:assign",
    }
    found = {
        (method.upper(), path): operation.get("x-permission")
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/users/{user_id}/role-assignments")
        for method, operation in operations.items()
    }
    assert found == expected


# --- list --------------------------------------------------------------------


async def test_list_happy_path(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    other = await make_user(db_session)
    app, viewer = await _stoktok(db_session)
    editor = await make_role(db_session, app, "stoktok:editor")
    a1 = await assign(db_session, user, viewer)
    a2 = await assign(db_session, user, editor, granted_by=other.id)
    await assign(db_session, other, viewer)  # another user's row is not listed

    resp = await rbac_client.get(_url(user.id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert (body["limit"], body["offset"]) == (20, 0)
    items = body["items"]
    assert all(set(i) == ASSIGNMENT_KEYS for i in items)
    # Same transaction => same created_at; ties are ordered by id.
    assert [i["id"] for i in items] == [str(a.id) for a in sorted([a1, a2], key=lambda a: a.id)]
    by_id = {i["id"]: i for i in items}
    assert by_id[str(a2.id)] == {
        "id": str(a2.id),
        "app": "stoktok",
        "role_id": str(editor.id),
        "role_key": "stoktok:editor",
        "org_id": None,
        "granted_by": str(other.id),
        "expires_at": None,
        "active": True,
        "created_at": by_id[str(a2.id)]["created_at"],
    }


async def test_list_order_created_at_desc(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    app, viewer = await _stoktok(db_session)
    editor = await make_role(db_session, app, "stoktok:editor")
    old = await assign(db_session, user, viewer)
    new = await assign(db_session, user, editor)
    old.created_at = _past(2)
    await db_session.flush()

    items = (await rbac_client.get(_url(user.id))).json()["items"]
    assert [i["id"] for i in items] == [str(new.id), str(old.id)]


async def test_list_filter_by_app(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    await assign(db_session, user, viewer)
    await assign(db_session, user, await super_admin_role(db_session))

    body = (await rbac_client.get(_url(user.id), params={"app": "admin"})).json()
    assert body["total"] == 1
    assert [i["role_key"] for i in body["items"]] == ["admin:super-admin"]


async def test_list_unknown_app_empty_page(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    await assign(db_session, user, viewer)

    resp = await rbac_client.get(_url(user.id), params={"app": "nope"})
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


async def test_list_bad_app_key_422(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    resp = await rbac_client.get(_url(user.id), params={"app": "Bad!"})
    _assert_error(resp, 422, "validation_error")


async def test_list_active_flag(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    app, viewer = await _stoktok(db_session)
    editor = await make_role(db_session, app, "stoktok:editor")
    owner = await make_role(db_session, app, "stoktok:owner")
    expired = await assign(db_session, user, viewer, expires_at=_past())
    open_ended = await assign(db_session, user, editor)
    future = await assign(db_session, user, owner, expires_at=_future())

    items = (await rbac_client.get(_url(user.id))).json()["items"]
    active = {i["id"]: i["active"] for i in items}
    assert active == {str(expired.id): False, str(open_ended.id): True, str(future.id): True}


async def test_list_unknown_user_404(rbac_client: AsyncClient) -> None:
    _assert_error(await rbac_client.get(_url(uuid.uuid4())), 404, "not_found")


async def test_list_pagination_limit_over_100_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    resp = await rbac_client.get(_url(user.id), params={"limit": 101})
    _assert_error(resp, 422, "validation_error")


# --- assign ------------------------------------------------------------------


async def test_assign_app_scope_201(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)

    resp = await rbac_client.post(_url(user.id), json={"role_id": str(viewer.id)})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == ASSIGNMENT_KEYS
    assert body["app"] == "stoktok"
    assert body["role_key"] == "stoktok:viewer"
    assert body["org_id"] is None
    assert body["granted_by"] is None
    assert body["expires_at"] is None
    assert body["active"] is True

    listed = (await rbac_client.get(_url(user.id))).json()["items"]
    assert listed == [body]


async def test_assign_org_scope_201(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session, org_mode="required")
    org = await _make_org(db_session)

    resp = await rbac_client.post(
        _url(user.id), json={"role_id": str(viewer.id), "org_id": str(org.id)}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["org_id"] == str(org.id)

    # App scope on a `required` app is allowed too (A4).
    resp = await rbac_client.post(_url(user.id), json={"role_id": str(viewer.id), "org_id": None})
    assert resp.status_code == 201, resp.text
    assert resp.json()["org_id"] is None


async def test_assign_unknown_user_404(rbac_client: AsyncClient) -> None:
    # 404 before any 422: the role is unknown too.
    resp = await rbac_client.post(_url(uuid.uuid4()), json={"role_id": str(uuid.uuid4())})
    _assert_error(resp, 404, "not_found")


async def test_assign_unknown_role_422(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    resp = await rbac_client.post(_url(user.id), json={"role_id": str(uuid.uuid4())})
    _assert_unprocessable(resp, "role_id", "unknown_role")


async def test_assign_naive_expires_at_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    naive = (datetime.now() + timedelta(days=1)).replace(tzinfo=None).isoformat()
    resp = await rbac_client.post(
        _url(user.id), json={"role_id": str(viewer.id), "expires_at": naive}
    )
    _assert_error(resp, 422, "validation_error")


async def test_assign_past_expires_at_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    resp = await rbac_client.post(
        _url(user.id), json={"role_id": str(viewer.id), "expires_at": _past().isoformat()}
    )
    _assert_unprocessable(resp, "expires_at", "expires_in_past")


async def test_assign_future_expires_at_201(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    resp = await rbac_client.post(
        _url(user.id), json={"role_id": str(viewer.id), "expires_at": _future().isoformat()}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["expires_at"] is not None
    assert resp.json()["active"] is True


async def test_assign_org_id_on_none_app_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)  # org_mode none
    org = await _make_org(db_session)
    resp = await rbac_client.post(
        _url(user.id), json={"role_id": str(viewer.id), "org_id": str(org.id)}
    )
    _assert_unprocessable(resp, "org_id", "org_id_not_allowed")


async def test_assign_unknown_org_422(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session, org_mode="required")
    resp = await rbac_client.post(
        _url(user.id), json={"role_id": str(viewer.id), "org_id": str(uuid.uuid4())}
    )
    _assert_unprocessable(resp, "org_id", "unknown_org")


async def test_assign_extra_field_422(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    resp = await rbac_client.post(
        _url(user.id), json={"role_id": str(viewer.id), "granted_by": str(user.id)}
    )
    _assert_error(resp, 422, "validation_error")


@pytest.mark.parametrize("org_scope", [False, True])
async def test_assign_active_duplicate_409(
    rbac_client: AsyncClient, db_session: AsyncSession, org_scope: bool
) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session, org_mode="required")
    body: dict[str, Any] = {"role_id": str(viewer.id)}
    if org_scope:
        body["org_id"] = str((await _make_org(db_session)).id)

    assert (await rbac_client.post(_url(user.id), json=body)).status_code == 201
    _assert_error(await rbac_client.post(_url(user.id), json=body), 409, "conflict")
    assert (await rbac_client.get(_url(user.id))).json()["total"] == 1


async def test_assign_expired_duplicate_replaced(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    old = await assign(db_session, user, viewer, expires_at=_past())
    before = (await rbac_client.get(_url(user.id))).json()["items"][0]
    assert before["active"] is False

    new_expiry = _future(7)
    resp = await rbac_client.post(
        _url(user.id), json={"role_id": str(viewer.id), "expires_at": new_expiry.isoformat()}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == str(old.id)
    assert body["created_at"] == before["created_at"]
    assert datetime.fromisoformat(body["expires_at"]) == new_expiry
    assert body["active"] is True

    listed = (await rbac_client.get(_url(user.id))).json()
    assert listed["total"] == 1
    assert listed["items"] == [body]


async def test_assign_super_admin_with_expiry_422(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    role = await super_admin_role(db_session)
    resp = await rbac_client.post(
        _url(user.id), json={"role_id": str(role.id), "expires_at": _future().isoformat()}
    )
    _assert_unprocessable(resp, "expires_at", "expiry_not_allowed")

    resp = await rbac_client.post(_url(user.id), json={"role_id": str(role.id)})
    assert resp.status_code == 201, resp.text
    assert resp.json()["role_key"] == "admin:super-admin"


async def test_assign_disabled_user_and_disabled_app_allowed(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    user.status = "disabled"
    _, viewer = await _stoktok(db_session, status="disabled")
    await db_session.flush()

    resp = await rbac_client.post(_url(user.id), json={"role_id": str(viewer.id)})
    assert resp.status_code == 201, resp.text


# --- revoke ------------------------------------------------------------------


async def test_revoke_204(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    ra = await assign(db_session, user, viewer)

    resp = await rbac_client.delete(_url(user.id, ra.id))
    assert resp.status_code == 204, resp.text
    assert (await rbac_client.get(_url(user.id))).json()["total"] == 0


async def test_revoke_twice_404(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    ra = await assign(db_session, user, viewer)

    assert (await rbac_client.delete(_url(user.id, ra.id))).status_code == 204
    _assert_error(await rbac_client.delete(_url(user.id, ra.id)), 404, "not_found")


async def test_revoke_unknown_404(rbac_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _assert_error(await rbac_client.delete(_url(user.id, uuid.uuid4())), 404, "not_found")
    _assert_error(await rbac_client.delete(_url(uuid.uuid4(), uuid.uuid4())), 404, "not_found")


async def test_revoke_other_users_assignment_404(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    other = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    ra = await assign(db_session, other, viewer)

    _assert_error(await rbac_client.delete(_url(user.id, ra.id)), 404, "not_found")
    assert (await rbac_client.get(_url(other.id))).json()["total"] == 1


async def _super_admin(db_session: AsyncSession, user: User, **kwargs: Any) -> uuid.UUID:
    return (await assign(db_session, user, await super_admin_role(db_session), **kwargs)).id


async def test_revoke_last_super_admin_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    ra_id = await _super_admin(db_session, user)

    _assert_error(await rbac_client.delete(_url(user.id, ra_id)), 409, "conflict")
    assert (await rbac_client.get(_url(user.id))).json()["total"] == 1


async def test_revoke_one_of_two_super_admins_204(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    first = await make_user(db_session)
    second = await make_user(db_session)
    first_ra = await _super_admin(db_session, first)
    second_ra = await _super_admin(db_session, second)

    assert (await rbac_client.delete(_url(first.id, first_ra))).status_code == 204
    _assert_error(await rbac_client.delete(_url(second.id, second_ra)), 409, "conflict")


async def test_revoke_super_admin_other_holder_disabled_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    active = await make_user(db_session)
    disabled = await make_user(db_session)
    disabled.status = "disabled"
    await db_session.flush()
    ra_id = await _super_admin(db_session, active)
    await _super_admin(db_session, disabled)

    _assert_error(await rbac_client.delete(_url(active.id, ra_id)), 409, "conflict")


async def test_revoke_expired_super_admin_204(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    ra_id = await _super_admin(db_session, user, expires_at=_past())

    assert (await rbac_client.delete(_url(user.id, ra_id))).status_code == 204


async def test_role_delete_with_assignment_409(
    rbac_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    _, viewer = await _stoktok(db_session)
    resp = await rbac_client.post(_url(user.id), json={"role_id": str(viewer.id)})
    assert resp.status_code == 201

    resp = await rbac_client.delete(f"/api/v1/apps/stoktok/roles/{viewer.id}")
    _assert_error(resp, 409, "conflict")
