import uuid
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.listing import sort_clauses
from app.modules.audit.models import AuditLog
from app.modules.organizations.models import Organization
from app.modules.organizations.repository import _SORT_COLUMNS
from app.modules.organizations.schemas import OrganizationSort
from tests.admin_auth_factories import bearer, make_staff, mint
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


# --- list filters and sort (ADM-020) -----------------------------------------

T0 = datetime(2026, 1, 1, tzinfo=UTC)


async def _org(
    db_session: AsyncSession,
    slug: str,
    name: str,
    *,
    status: str = "active",
    created_at: datetime = T0,
) -> Organization:
    org = Organization(slug=slug, name=name, status=status, created_at=created_at)
    db_session.add(org)
    await db_session.flush()
    await db_session.refresh(org)
    return org


def _slugs(body: dict) -> list[str]:
    return [item["slug"] for item in body["items"]]


async def test_sort_allow_list_matches_repository_mapping() -> None:
    for value in get_args(OrganizationSort):
        sort_clauses(value, _SORT_COLUMNS, Organization.id)


async def test_list_filter_status(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    await _org(db_session, "alpha", "Alpha")
    await _org(db_session, "bravo", "Bravo", status="suspended")

    body = (await admin_client.get(BASE, params={"status": "suspended"})).json()
    assert _slugs(body) == ["bravo"]
    assert body["total"] == 1
    body = (await admin_client.get(BASE, params={"status": "active"})).json()
    assert _slugs(body) == ["alpha"]
    assert body["total"] == 1


async def test_list_filter_q_name_or_slug_case_insensitive(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _org(db_session, "one", "Alpha")
    await _org(db_session, "bravo", "Other")
    await _org(db_session, "three", "Charlie")

    assert _slugs((await admin_client.get(BASE, params={"q": "ALP"})).json()) == ["one"]
    assert _slugs((await admin_client.get(BASE, params={"q": "brav"})).json()) == ["bravo"]


async def test_list_filter_q_is_trimmed(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _org(db_session, "one", "Alpha")
    await _org(db_session, "two", "Bravo")

    body = (await admin_client.get(BASE, params={"q": " alpha "})).json()
    assert _slugs(body) == ["one"]


@pytest.mark.parametrize(
    ("term", "expected"),
    [("%", ["pure"]), ("a_b", ["ab"]), ("_", ["ab"]), ("\\", ["back"])],
)
async def test_list_filter_q_escapes_like_wildcards(
    admin_client: AsyncClient, db_session: AsyncSession, term: str, expected: list[str]
) -> None:
    await _org(db_session, "pure", "100% Pure")
    await _org(db_session, "plain", "100 Pure")
    await _org(db_session, "ab", "a_b")
    await _org(db_session, "axb", "axb")
    await _org(db_session, "back", "back\\slash")

    resp = await admin_client.get(BASE, params={"q": term})
    assert resp.status_code == 200
    assert _slugs(resp.json()) == expected


async def test_list_filters_combined(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    await _org(db_session, "alpha", "Alpha", status="suspended")
    await _org(db_session, "alphb", "Alphb")
    await _org(db_session, "bravo", "Bravo", status="suspended")

    body = (await admin_client.get(BASE, params={"status": "suspended", "q": "alph"})).json()
    assert _slugs(body) == ["alpha"]
    assert body["total"] == 1


async def test_list_total_is_filtered_count(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    for i in range(3):
        await _org(db_session, f"match{i}", f"Match {i}")
    for i in range(2):
        await _org(db_session, f"other{i}", f"Other {i}")

    body = (await admin_client.get(BASE, params={"q": "match", "limit": 1})).json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert body["limit"] == 1


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("created_at", ["b", "c", "a"]),
        ("-created_at", ["a", "c", "b"]),
        ("name", ["c", "a", "b"]),
        ("-name", ["b", "a", "c"]),
        ("slug", ["a", "b", "c"]),
        ("-slug", ["c", "b", "a"]),
    ],
)
async def test_list_sort_orders(
    admin_client: AsyncClient, db_session: AsyncSession, sort: str, expected: list[str]
) -> None:
    # created_at order: b < c < a; name order: c < a < b; slug order: a < b < c.
    await _org(db_session, "a", "Bravo", created_at=T0 + timedelta(days=3))
    await _org(db_session, "b", "Charlie", created_at=T0 + timedelta(days=1))
    await _org(db_session, "c", "Alpha", created_at=T0 + timedelta(days=2))

    resp = await admin_client.get(BASE, params={"sort": sort})
    assert resp.status_code == 200
    assert _slugs(resp.json()) == [{"a": "a", "b": "b", "c": "c"}[s] for s in expected]


@pytest.mark.parametrize("sort", ["name", "-name", "created_at", "-created_at"])
async def test_list_sort_tie_breaker_is_id_asc(
    admin_client: AsyncClient, db_session: AsyncSession, sort: str
) -> None:
    first = await _org(db_session, "one", "Same", created_at=T0)
    second = await _org(db_session, "two", "Same", created_at=T0)
    other = await _org(db_session, "three", "Other", created_at=T0 + timedelta(days=1))
    tied = sorted([str(first.id), str(second.id)])

    items = (await admin_client.get(BASE, params={"sort": sort})).json()["items"]
    ids = [item["id"] for item in items if item["id"] != str(other.id)]
    assert ids == tied


async def test_list_default_order_unchanged(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _org(db_session, "one", "One", created_at=T0)
    await _org(db_session, "two", "Two", created_at=T0 + timedelta(days=1))
    await _org(db_session, "three", "Three", created_at=T0 + timedelta(days=1))

    default = (await admin_client.get(BASE)).json()
    explicit = (await admin_client.get(BASE, params={"sort": "-created_at"})).json()
    ids = [i["id"] for i in default["items"]]
    newest = sorted(ids[:2])
    assert ids[:2] == newest
    assert [i["slug"] for i in default["items"]][2] == "one"
    assert default["items"] == explicit["items"]


@pytest.mark.parametrize(
    "params",
    [
        {"status": "deleted"},
        {"sort": "bogus"},
        {"sort": "id"},
        {"sort": "NAME"},
        {"q": ""},
        {"q": "   "},
        {"q": "x" * 101},
    ],
)
async def test_list_rejects_bad_query(admin_client: AsyncClient, params: dict[str, str]) -> None:
    resp = await admin_client.get(BASE, params=params)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_list_without_permission_403(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session)
    headers = bearer(mint(staff))
    for query in ("q=acme&sort=name", "sort=bogus"):
        resp = await client.get(f"{BASE}?{query}", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "permission_denied"
        assert resp.json()["error"]["details"] == {"required": "admin:organizations:read"}
