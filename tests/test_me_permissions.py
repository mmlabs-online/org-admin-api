"""GET /api/v1/me/permissions: the caller's own admin authorization (USR-11, ADR-0008)."""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Permission, RoleAssignment
from app.modules.rbac.schemas import AdminOriginsSet
from app.modules.rbac.service import AppService
from tests.admin_auth_factories import bearer, make_staff, mint
from tests.rbac_factories import assign, grant, make_role, super_admin_role

URL = "/api/v1/me/permissions"
ORGS = "/api/v1/organizations"
ADMIN_ORIGIN = "https://admin-portal.mmlabs.online"
UNAUTHORIZED = {"code": "unauthorized", "message": "Authentication required", "details": None}


async def test_no_token_401(client: AsyncClient) -> None:
    resp = await client.get(URL)
    assert resp.status_code == 401
    assert resp.json() == {"error": UNAUTHORIZED}
    assert resp.headers["www-authenticate"] == 'Bearer realm="admin"'
    assert resp.headers["cache-control"] == "no-store"


async def test_other_audience_401(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:users:read")
    resp = await client.get(URL, headers=bearer(mint(staff, aud="stoktok")))
    assert resp.status_code == 401
    assert resp.json() == {"error": UNAUTHORIZED}
    assert resp.headers["www-authenticate"] == 'Bearer realm="admin", error="invalid_token"'


async def test_list_audience_401(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:users:read")
    resp = await client.get(URL, headers=bearer(mint(staff, aud=["admin"])))
    assert resp.status_code == 401
    assert resp.json() == {"error": UNAUTHORIZED}


async def test_returns_own_data_only(client: AsyncClient, db_session: AsyncSession) -> None:
    a = await make_staff(db_session, "admin:organizations:read")
    b = await make_staff(db_session, "admin:users:read")
    resp_a = await client.get(URL, headers=bearer(mint(a)))
    resp_b = await client.get(URL, headers=bearer(mint(b)))
    assert resp_a.status_code == resp_b.status_code == 200
    assert resp_a.json()["user_id"] == str(a.id)
    assert resp_a.json()["permissions"] == ["admin:organizations:read"]
    assert resp_b.json()["user_id"] == str(b.id)
    assert resp_b.json()["permissions"] == ["admin:users:read"]
    assert str(b.id) not in resp_a.text
    assert "admin:users:read" not in resp_a.text


async def test_query_user_id_ignored(client: AsyncClient, db_session: AsyncSession) -> None:
    a = await make_staff(db_session, "admin:organizations:read")
    b = await make_staff(db_session, "admin:users:read")
    resp = await client.get(URL, params={"user_id": str(b.id)}, headers=bearer(mint(a)))
    assert resp.status_code == 200
    assert resp.json()["user_id"] == str(a.id)
    assert resp.json()["permissions"] == ["admin:organizations:read"]


async def test_exact_keys_no_pii(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:users:read")
    resp = await client.get(URL, headers=bearer(mint(staff)))
    assert set(resp.json()) == {"user_id", "roles", "permissions"}
    assert staff.email not in resp.text


async def test_super_admin_wildcard_unexpanded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await make_staff(db_session)
    await assign(db_session, staff, await super_admin_role(db_session))
    resp = await client.get(URL, headers=bearer(mint(staff)))
    assert resp.status_code == 200
    assert resp.json() == {
        "user_id": str(staff.id),
        "roles": ["admin:super-admin"],
        "permissions": ["admin:*"],
    }


async def test_permissions_and_roles_sorted(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:users:read", "admin:apps:read")
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    perm = await db_session.scalar(
        select(Permission).where(
            Permission.app_id == admin.id, Permission.key == "admin:audit:read"
        )
    )
    assert perm is not None
    role = await make_role(db_session, admin, "admin:aaa-first")
    await grant(db_session, role, perm)
    await assign(db_session, staff, role)
    body = (await client.get(URL, headers=bearer(mint(staff)))).json()
    assert len(body["roles"]) == 2
    assert body["roles"] == sorted(body["roles"])
    assert len(body["permissions"]) == 3
    assert body["permissions"] == sorted(body["permissions"])


async def test_grant_visible_on_next_request(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:users:read")
    headers = bearer(mint(staff))
    first = (await client.get(URL, headers=headers)).json()
    assert first["permissions"] == ["admin:users:read"]
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    perm = await db_session.scalar(
        select(Permission).where(Permission.app_id == admin.id, Permission.key == "admin:apps:read")
    )
    assert perm is not None
    role = await make_role(db_session, admin, "admin:second-role")
    await grant(db_session, role, perm)
    await assign(db_session, staff, role)
    second = (await client.get(URL, headers=headers)).json()
    assert "admin:second-role" in second["roles"]
    assert second["permissions"] == ["admin:apps:read", "admin:users:read"]


async def test_revoke_visible_on_next_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await make_staff(db_session, "admin:users:read")
    headers = bearer(mint(staff))
    assert (await client.get(URL, headers=headers)).json()["roles"] != []
    await db_session.execute(delete(RoleAssignment).where(RoleAssignment.user_id == staff.id))
    resp = await client.get(URL, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"user_id": str(staff.id), "roles": [], "permissions": []}


async def test_expired_assignment_dropped(client: AsyncClient, db_session: AsyncSession) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    staff = await make_staff(db_session, "admin:users:read", expires_at=past)
    resp = await client.get(URL, headers=bearer(mint(staff)))
    assert resp.status_code == 200
    assert resp.json() == {"user_id": str(staff.id), "roles": [], "permissions": []}


async def test_disabled_caller_401(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:organizations:read")
    headers = bearer(mint(staff))
    staff.status = "disabled"
    await db_session.flush()
    resp = await client.get(URL, headers=headers)
    other = await client.get(ORGS, headers=headers)
    assert resp.status_code == other.status_code == 401
    assert resp.json() == other.json() == {"error": UNAUTHORIZED}
    assert resp.headers["www-authenticate"] == other.headers["www-authenticate"]


async def test_unverified_caller_401(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:organizations:read")
    headers = bearer(mint(staff))
    staff.email_verified_at = None
    await db_session.flush()
    resp = await client.get(URL, headers=headers)
    other = await client.get(ORGS, headers=headers)
    assert resp.status_code == other.status_code == 401
    assert resp.json() == other.json() == {"error": UNAUTHORIZED}
    assert resp.headers["www-authenticate"] == other.headers["www-authenticate"]


async def test_org_scoped_admin_assignment_ignored(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    staff = await make_staff(db_session)
    org = Organization(slug="acme-me", name="Acme")
    db_session.add(org)
    await db_session.flush()
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    perm = await db_session.scalar(
        select(Permission).where(
            Permission.app_id == admin.id, Permission.key == "admin:users:read"
        )
    )
    assert perm is not None
    role = await make_role(db_session, admin, "admin:org-scoped")
    await grant(db_session, role, perm)
    await assign(db_session, staff, role, org_id=org.id)
    resp = await client.get(URL, headers=bearer(mint(staff)))
    assert resp.status_code == 200
    assert resp.json() == {"user_id": str(staff.id), "roles": [], "permissions": []}


async def test_cache_control_no_store_on_200(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:users:read")
    resp = await client.get(URL, headers=bearer(mint(staff)))
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert "etag" not in resp.headers


async def test_admin_cors_on_me_permissions(client: AsyncClient, db_session: AsyncSession) -> None:
    await AppService(db_session).set_admin_origins(
        AdminOriginsSet(origins=[ADMIN_ORIGIN]), app_env="test"
    )
    staff = await make_staff(db_session, "admin:users:read")
    resp = await client.get(URL, headers={**bearer(mint(staff)), "Origin": ADMIN_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ADMIN_ORIGIN
    assert "access-control-allow-credentials" not in resp.headers
    assert "Origin" in resp.headers["vary"]
