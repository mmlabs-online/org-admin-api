"""ADM-033: staff list, detail, live and delete on /api/v1/releases."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.modules.audit.models import AuditLog
from app.modules.bug_reports.models import BugReport
from app.modules.rbac.models import App
from app.modules.releases.models import AppRelease
from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.rbac_factories import make_app, make_user
from tests.release_factories import make_release

URL = "/api/v1/releases"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
CHANGE = {
    "pr_url": "https://github.com/mmlabs-online/x/pull/1",
    "title": "Secret <b>title</b>",
    "ticket_id": "ADM-001",
    "risk": "low",
}
IMAGE = {"name": "api", "digest": "sha256:" + "a" * 64}


def _key() -> str:
    return f"app-{uuid.uuid4().hex[:8]}"


def _deployed(db: AsyncSession, app: App, at: datetime, **kw: Any) -> Any:
    return make_release(db, app, status="deployed", deployed_at=at, created_at=at, **kw)


@pytest.fixture
async def staff(db_session: AsyncSession) -> User:
    return await make_user(db_session)


async def _get(make_client: MakeClient, staff: User, path: str = "", **params: Any) -> Any:
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as c:
        return await c.get(URL + path, params=params)


# --- auth and OpenAPI -------------------------------------------------------------


async def test_routes_401_without_token(make_client: MakeClient) -> None:
    rid = uuid.uuid4()
    async with make_client() as c:
        assert (await c.get(URL)).status_code == 401
        assert (await c.get(f"{URL}/live")).status_code == 401
        assert (await c.get(f"{URL}/{rid}")).status_code == 401
        assert (await c.delete(f"{URL}/{rid}")).status_code == 401


async def test_routes_403_without_wildcard(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    release = await make_release(db_session, await make_app(db_session, _key()))
    perms = ["admin:apps:read", "admin:bug-reports:read"]
    async with make_client(admin_permissions=perms, admin_user=staff) as c:
        assert (await c.get(URL)).status_code == 403
        assert (await c.get(f"{URL}/live")).status_code == 403
        assert (await c.get(f"{URL}/{release.id}")).status_code == 403
        assert (await c.delete(f"{URL}/{release.id}")).status_code == 403
    assert await db_session.get(AppRelease, release.id) is not None


def test_openapi_declares_admin_wildcard() -> None:
    paths = create_app().openapi()["paths"]
    ops = [
        paths[URL]["get"],
        paths[f"{URL}/live"]["get"],
        paths[f"{URL}/{{release_id}}"]["get"],
        paths[f"{URL}/{{release_id}}"]["delete"],
    ]
    assert [op["x-permission"] for op in ops] == ["admin:*"] * 4


def test_openapi_release_schemas() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    for name in (
        "ReleaseSummary",
        "ReleaseDetail",
        "ReleaseChange",
        "ReleaseImage",
        "ReleaseEnvironment",
        "ReleaseStatus",
        "ChangeRisk",
    ):
        assert name in schemas
    summary = schemas["ReleaseSummary"]["properties"]
    assert "changes" not in summary and "images" not in summary
    for field in ("git_sha", "deployment_pr", "has_migrations", "deployed_at"):
        assert {"type": "null"} in summary[field]["anyOf"]
    assert {"changes", "images"} <= set(schemas["ReleaseDetail"]["properties"])


# --- list -----------------------------------------------------------------------


async def test_list_newest_first_with_id_tiebreak(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    old = await make_release(db_session, app, created_at=T0)
    t1 = await make_release(db_session, app, created_at=T0 + timedelta(days=1))
    t2 = await make_release(db_session, app, created_at=T0 + timedelta(days=1))
    body = (await _get(make_client, staff)).json()
    tied = sorted([str(t1.id), str(t2.id)], reverse=True)
    assert [i["id"] for i in body["items"]] == [*tied, str(old.id)]


async def test_list_filters(make_client: MakeClient, db_session: AsyncSession, staff: User) -> None:
    a1, a2 = await make_app(db_session, _key()), await make_app(db_session, _key())
    r1 = await make_release(db_session, a1, created_at=T0)
    r2 = await make_release(
        db_session, a1, environment="staging", created_at=T0 + timedelta(days=1)
    )
    r3 = await _deployed(db_session, a2, T0 + timedelta(days=2))

    async def ids(**p: Any) -> set[str]:
        resp = await _get(make_client, staff, **p)
        assert resp.status_code == 200
        return {i["id"] for i in resp.json()["items"]}

    assert await ids(app=a1.key) == {str(r1.id), str(r2.id)}
    assert await ids(environment="staging", app=a1.key) == {str(r2.id)}
    assert await ids(status="deployed", app=a2.key) == {str(r3.id)}
    frm = (T0 + timedelta(days=1)).isoformat()
    assert await ids(app=a1.key, created_from=frm) == {str(r2.id)}  # inclusive
    assert await ids(app=a1.key, created_to=frm) == {str(r1.id)}  # exclusive
    assert await ids(app=a1.key, created_from=T0.isoformat(), created_to=frm) == {str(r1.id)}


async def test_list_sort_deployed_at_nulls_last(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    d1 = await _deployed(db_session, app, T0 + timedelta(days=5))
    d2 = await _deployed(db_session, app, T0 + timedelta(days=1))
    p = await make_release(db_session, app, created_at=T0 + timedelta(days=9))
    body = (await _get(make_client, staff, sort="deployed_at", app=app.key)).json()
    assert [i["id"] for i in body["items"]] == [str(d1.id), str(d2.id), str(p.id)]


async def test_list_paging_and_bounds(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    for n in range(3):
        await make_release(db_session, app, created_at=T0 + timedelta(days=n))
    body = (await _get(make_client, staff, app=app.key, limit=2, offset=1)).json()
    assert (body["total"], body["limit"], body["offset"], len(body["items"])) == (3, 2, 1, 2)
    assert (await _get(make_client, staff, limit=101)).status_code == 422
    assert (await _get(make_client, staff, limit=0)).status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"environment": "prod"},
        {"status": "x"},
        {"sort": "x"},
        {"app": "Bad_Key"},
        {"created_from": "2026-01-01T00:00:00"},
        {"created_from": "2026-02-01T00:00:00Z", "created_to": "2026-01-01T00:00:00Z"},
    ],
)
async def test_list_validation_422(make_client: MakeClient, staff: User, params: Any) -> None:
    assert (await _get(make_client, staff, **params)).status_code == 422


async def test_list_unknown_app_is_empty_page(make_client: MakeClient, staff: User) -> None:
    resp = await _get(make_client, staff, app="no-such-app")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


async def test_list_items_have_no_bodies(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    await make_release(db_session, app, images=[IMAGE], changes=[CHANGE, CHANGE])
    item = (await _get(make_client, staff, app=app.key)).json()["items"][0]
    assert "changes" not in item and "images" not in item
    assert item["changes_count"] == 2
    assert item["app_key"] == app.key


# --- detail ---------------------------------------------------------------------


async def test_release_detail_bug_report_counts(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    user = await make_user(db_session)
    rel = await make_release(db_session, app)
    link = {"release_id": rel.id, "release_match": "reported"}
    db_session.add_all(
        [
            BugReport(app_id=app.id, user_id=user.id, description="a", **link),
            BugReport(app_id=app.id, user_id=user.id, description="b", **link),
            BugReport(
                app_id=app.id,
                user_id=user.id,
                description="c",
                status="done",
                handled_at=datetime.now(UTC),
                **link,
            ),
            BugReport(app_id=app.id, user_id=user.id, description="unlinked"),
        ]
    )
    await db_session.flush()
    body = (await _get(make_client, staff, f"/{rel.id}")).json()
    assert body["bug_reports"] == {"open": 2, "done": 1}


async def test_release_detail_counts_zero_when_no_reports(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    rel = await make_release(db_session, await make_app(db_session, _key()))
    body = (await _get(make_client, staff, f"/{rel.id}")).json()
    assert body["bug_reports"] == {"open": 0, "done": 0}


async def test_detail_full_and_thin_record(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    full = await make_release(
        db_session,
        app,
        images=[IMAGE],
        changes=[CHANGE],
        deployment_pr="https://github.com/mmlabs-online/deployment/pull/3",
        has_migrations=True,
    )
    body = (await _get(make_client, staff, f"/{full.id}")).json()
    assert body["images"] == [IMAGE]
    assert body["changes"] == [CHANGE]
    assert body["changes_count"] == 1
    assert body["has_migrations"] is True
    assert body["created_via"] == "app"
    thin = await make_release(
        db_session, app, git_sha=None, created_via="deployer", version="v9.9.9"
    )
    body = (await _get(make_client, staff, f"/{thin.id}")).json()
    assert body["git_sha"] is None and body["has_migrations"] is None
    assert body["deployment_pr"] is None and body["deployed_at"] is None
    assert body["images"] == [] and body["changes"] == []


async def test_detail_unknown_404_and_bad_id_422(make_client: MakeClient, staff: User) -> None:
    resp = await _get(make_client, staff, f"/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert (await _get(make_client, staff, "/not-a-uuid")).status_code == 422


# --- live -----------------------------------------------------------------------


async def test_live_rollback_returns_redeployed_older_version(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    await _deployed(db_session, app, T0 + timedelta(days=2), version="v1.3.0")
    older = await _deployed(db_session, app, T0 + timedelta(days=3), version="v1.2.0")
    body = (await _get(make_client, staff, "/live", app=app.key)).json()
    assert [i["id"] for i in body["items"]] == [str(older.id)]
    assert body["total"] == 1


async def test_live_ignores_failed_and_pending(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    good = await _deployed(db_session, app, T0, version="v1.0.0")
    await make_release(
        db_session, app, status="failed", version="v1.4.0", created_at=T0 + timedelta(days=5)
    )
    await make_release(db_session, app, version="v1.5.0", created_at=T0 + timedelta(days=6))
    body = (await _get(make_client, staff, "/live", app=app.key)).json()
    assert [i["id"] for i in body["items"]] == [str(good.id)]


async def test_live_environments_do_not_leak(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    a1 = await make_app(db_session, _key())
    a2 = await make_app(db_session, _key())
    a3 = await make_app(db_session, _key())
    sha = "abcdef0" + "1" * 33
    prod = await _deployed(db_session, a1, T0, version="v1.0.0")
    stg = await _deployed(
        db_session, a1, T0 + timedelta(days=1), environment="staging", git_sha=sha
    )
    other = await _deployed(db_session, a2, T0, version="v1.0.0")
    await make_release(db_session, a3)  # only pending
    items = (await _get(make_client, staff, "/live", limit=100)).json()["items"]
    mine = {i["id"]: i for i in items}
    assert {str(prod.id), str(stg.id), str(other.id)} <= set(mine)
    assert not any(i["app_key"] == a3.key for i in items)
    assert [i["app_key"] for i in items] == sorted(i["app_key"] for i in items)
    body = (await _get(make_client, staff, "/live", app=a1.key)).json()
    assert [i["environment"] for i in body["items"]] == ["production", "staging"]
    body = (await _get(make_client, staff, "/live", app=a1.key, environment="staging")).json()
    assert [i["id"] for i in body["items"]] == [str(stg.id)]
    assert (await _get(make_client, staff, "/live", app="no-such-app")).json()["total"] == 0


async def test_live_tie_breaks_on_id(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    r1 = await _deployed(db_session, app, T0, version="v1.0.0")
    r2 = await _deployed(db_session, app, T0, version="v1.0.1")
    body = (await _get(make_client, staff, "/live", app=app.key)).json()
    assert [i["id"] for i in body["items"]] == [str(max(r1.id, r2.id))]


async def test_live_route_not_shadowed(make_client: MakeClient, staff: User) -> None:
    assert (await _get(make_client, staff, "/live")).status_code == 200


# --- delete ---------------------------------------------------------------------


async def _audit(db: AsyncSession) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.target_type == "release")
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_delete_writes_one_audit_row_ids_only(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    release = await make_release(db_session, app, version="v2.0.0", changes=[CHANGE])
    release_id = release.id
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as c:
        resp = await c.delete(f"{URL}/{release_id}")
    assert resp.status_code == 204 and resp.content == b""
    assert await db_session.scalar(select(func.count()).select_from(AppRelease)) == 0
    (row,) = await _audit(db_session)
    assert row.action == "release.deleted"
    assert (row.target_type, row.target_id, row.app_key) == ("release", release_id, app.key)
    assert row.actor_user_id == staff.id
    assert row.changes == {
        "environment": {"from": "production", "to": None},
        "version": {"from": "v2.0.0", "to": None},
        "status": {"from": "pending", "to": None},
        "changes_count": {"from": 1, "to": None},
    }
    dumped = str(row.changes) + str(row.target_id)
    assert "Secret" not in dumped and "https://" not in dumped


async def test_delete_unknown_404_writes_no_row(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    release = await make_release(db_session, await make_app(db_session, _key()))
    release_id = release.id
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as c:
        assert (await c.delete(f"{URL}/{uuid.uuid4()}")).status_code == 404
        assert (await c.delete(f"{URL}/{release_id}")).status_code == 204
        assert (await c.delete(f"{URL}/{release_id}")).status_code == 404
    assert len(await _audit(db_session)) == 1


# --- other apps -------------------------------------------------------------------


async def test_admin_app_releases_listed(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    release = await make_release(db_session, admin, version="v7.7.7")
    body = (await _get(make_client, staff, app="admin")).json()
    assert [i["id"] for i in body["items"]] == [str(release.id)]


async def test_app_delete_with_release_is_409(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await make_app(db_session, _key())
    await make_release(db_session, app)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as c:
        resp = await c.delete(f"/api/v1/apps/{app.key}")
    assert resp.status_code == 409
