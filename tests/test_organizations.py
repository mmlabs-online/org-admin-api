import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from tests.rbac_factories import assign, make_app, make_role, make_user

BASE = "/api/v1/organizations"


async def _create(admin_client: AsyncClient, slug: str = "acme", name: str = "Acme") -> dict:
    resp = await admin_client.post(BASE, json={"slug": slug, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_and_get(admin_client: AsyncClient) -> None:
    org = await _create(admin_client)
    assert org["status"] == "active"

    resp = await admin_client.get(f"{BASE}/{org['id']}")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "acme"


async def test_duplicate_slug_conflicts(admin_client: AsyncClient) -> None:
    await _create(admin_client)
    resp = await admin_client.post(BASE, json={"slug": "acme", "name": "Other"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_invalid_slug_is_rejected(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(BASE, json={"slug": "Not Valid!", "name": "x"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_get_missing_returns_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(f"{BASE}/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_list_paginates(admin_client: AsyncClient) -> None:
    for i in range(3):
        await _create(admin_client, slug=f"org-{i}", name=f"Org {i}")

    resp = await admin_client.get(BASE, params={"limit": 2, "offset": 0})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 3
    assert len(body["items"]) == 2

    resp = await admin_client.get(BASE, params={"limit": 2, "offset": 2})
    assert len(resp.json()["items"]) == 1


async def test_update_status_and_delete(admin_client: AsyncClient) -> None:
    org = await _create(admin_client)

    resp = await admin_client.patch(f"{BASE}/{org['id']}", json={"status": "suspended"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"

    resp = await admin_client.delete(f"{BASE}/{org['id']}")
    assert resp.status_code == 204
    assert (await admin_client.get(f"{BASE}/{org['id']}")).status_code == 404


async def test_admin_org_read_has_optional_created_by(admin_client: AsyncClient) -> None:
    org = await _create(admin_client)
    assert org["created_by"] is None
    got = await admin_client.get(f"{BASE}/{org['id']}")
    assert got.json()["created_by"] is None


async def _with_assignment(
    admin_client: AsyncClient, db_session: AsyncSession, *, expired: bool = False
) -> dict:
    org = await _create(admin_client)
    app = await make_app(db_session, "calechip", org_mode="required")
    role = await make_role(db_session, app, "calechip:viewer")
    user = await make_user(db_session)
    expires = datetime(2000, 1, 1, tzinfo=UTC) if expired else None
    await assign(db_session, user, role, org_id=uuid.UUID(org["id"]), expires_at=expires)
    return org


async def test_delete_org_with_assignments_409(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _with_assignment(admin_client, db_session)
    resp = await admin_client.delete(f"{BASE}/{org['id']}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "org_has_assignments"
    assert (await admin_client.get(f"{BASE}/{org['id']}")).status_code == 200
    deleted = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "organization.deleted")
    )
    assert deleted == 0


async def test_delete_org_with_only_expired_assignment_409(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _with_assignment(admin_client, db_session, expired=True)
    resp = await admin_client.delete(f"{BASE}/{org['id']}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "org_has_assignments"


async def test_delete_org_without_assignments_204(admin_client: AsyncClient) -> None:
    org = await _create(admin_client)
    assert (await admin_client.delete(f"{BASE}/{org['id']}")).status_code == 204
