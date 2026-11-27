"""Case-insensitive, locale-aware `sort=name` on organizations and apps (ADM-026)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.models import Organization
from app.modules.rbac.models import App
from app.modules.rbac.repository import name_sort_key
from tests.conftest import MakeClient

ORGS = "/api/v1/organizations"
APPS = "/api/v1/apps"
NAMES = ["Beta", "alpha", "Ánh", "Zed", "đức", "Dung"]
# ICU root: đ is d with a stroke (secondary difference), so "đức" < "Dung" on c < n.
EXPECTED_ASC = ["alpha", "Ánh", "Beta", "đức", "Dung", "Zed"]
TIE_NAMES = ["Same", "same", "ân", "Ân"]


@pytest.fixture
async def staff_client(make_client: MakeClient) -> AsyncClient:  # type: ignore[misc]
    async with make_client(admin_permissions=["admin:*"]) as c:
        yield c


async def _seed_orgs(db: AsyncSession, names: list[str]) -> dict[str, str]:
    """Returns {id: name}."""
    out: dict[str, str] = {}
    for i, name in enumerate(names):
        org = Organization(slug=f"ns-{i}-{name.lower()[:3]}".replace(" ", ""), name=name)
        db.add(org)
        await db.flush()
        out[str(org.id)] = name
    return out


async def _seed_apps(db: AsyncSession, names: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, name in enumerate(names):
        app = App(key=f"ns-{i}", name=name)
        db.add(app)
        await db.flush()
        out[str(app.id)] = name
    return out


def _mine(resp_json: dict, seeded: dict[str, str]) -> list[tuple[str, str]]:
    return [(i["id"], i["name"]) for i in resp_json["items"] if i["id"] in seeded]


async def test_icu_collation_available(db_session: AsyncSession) -> None:
    row = await db_session.scalar(text("SELECT 1 FROM pg_collation WHERE collname = 'und-x-icu'"))
    encoding = await db_session.scalar(text("SHOW server_encoding"))
    assert row == 1 and encoding == "UTF8", (
        "Postgres test DB lacks ICU collation und-x-icu (or is not UTF8): "
        "sort=name would 500 - see ADM-026 plan"
    )


def test_name_sort_key_compiles_to_icu_lower() -> None:
    sql = str(
        select(Organization)
        .order_by(name_sort_key(Organization.name))
        .compile(dialect=postgresql.dialect())
    )
    assert 'lower(organizations.name COLLATE "und-x-icu")' in sql


@pytest.mark.parametrize("sort", ["name", "-name"])
async def test_orgs_name_sort_case_and_accents(
    staff_client: AsyncClient, db_session: AsyncSession, sort: str
) -> None:
    seeded = await _seed_orgs(db_session, NAMES)
    resp = await staff_client.get(ORGS, params={"sort": sort, "limit": 100})
    got = [n for _, n in _mine(resp.json(), seeded)]
    assert got == (EXPECTED_ASC if sort == "name" else EXPECTED_ASC[::-1])


@pytest.mark.parametrize("sort", ["name", "-name"])
async def test_apps_name_sort_case_and_accents(
    staff_client: AsyncClient, db_session: AsyncSession, sort: str
) -> None:
    seeded = await _seed_apps(db_session, NAMES)
    resp = await staff_client.get(APPS, params={"sort": sort, "limit": 100})
    got = [n for _, n in _mine(resp.json(), seeded)]
    assert got == (EXPECTED_ASC if sort == "name" else EXPECTED_ASC[::-1])


def _assert_ties_by_id(got: list[tuple[str, str]], sort: str) -> None:
    # Groups of case-only equal names; inside a group `id` is ascending in both directions.
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for id_, name in got:
        folded = name.lower()
        if folded not in groups:
            order.append(folded)
        groups.setdefault(folded, []).append(id_)
    assert sorted(order) == sorted({"same", "ân"})
    assert order == (["ân", "same"] if sort == "name" else ["same", "ân"])
    for ids in groups.values():
        assert len(ids) == 2
        assert ids == sorted(ids)


@pytest.mark.parametrize("sort", ["name", "-name"])
async def test_orgs_name_case_only_tie_broken_by_id(
    staff_client: AsyncClient, db_session: AsyncSession, sort: str
) -> None:
    seeded = await _seed_orgs(db_session, TIE_NAMES)
    resp = await staff_client.get(ORGS, params={"sort": sort, "limit": 100})
    _assert_ties_by_id(_mine(resp.json(), seeded), sort)


@pytest.mark.parametrize("sort", ["name", "-name"])
async def test_apps_name_case_only_tie_broken_by_id(
    staff_client: AsyncClient, db_session: AsyncSession, sort: str
) -> None:
    seeded = await _seed_apps(db_session, TIE_NAMES)
    resp = await staff_client.get(APPS, params={"sort": sort, "limit": 100})
    _assert_ties_by_id(_mine(resp.json(), seeded), sort)
