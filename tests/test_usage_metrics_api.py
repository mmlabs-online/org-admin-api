"""ADM-039: staff CRUD on /api/v1/apps/{key}/usage-metrics."""

import itertools
import logging
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.rbac.models import App
from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.rbac_factories import make_app, make_user

V1 = "/api/v1"
SUPER = ["admin:*"]
_seq = itertools.count(1)


def _url(app_key: str, metric_id: object = "") -> str:
    return f"{V1}/apps/{app_key}/usage-metrics" + (f"/{metric_id}" if metric_id else "")


def _body(key: str = "chat-turns", **kw: Any) -> dict[str, Any]:
    return {
        "key": key,
        "display_name": "Chat turns",
        "unit": "count",
        "default_limit": 20,
        "reason": "initial",
        **kw,
    }


async def _app(db: AsyncSession, **kw: Any) -> App:
    return await make_app(db, f"app-{next(_seq)}", **kw)


async def _create(c: Any, app_key: str, **kw: Any) -> dict[str, Any]:
    resp = await c.post(_url(app_key), json=_body(**kw))
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def _audit(db: AsyncSession) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.target_type == "usage_metric")
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


@pytest.fixture
async def staff(db_session: AsyncSession) -> User:
    return await make_user(db_session)


# --- auth -------------------------------------------------------------------------


async def test_routes_401_without_token(make_client: MakeClient) -> None:
    mid = uuid.uuid4()
    async with make_client() as c:
        assert (await c.get(_url("shop"))).status_code == 401
        assert (await c.get(_url("shop", mid))).status_code == 401
        assert (await c.post(_url("shop"), json=_body())).status_code == 401
        assert (await c.patch(_url("shop", mid), json={})).status_code == 401


async def test_reads_403_without_read(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=["admin:usage:write"]) as c:
        for resp in (await c.get(_url(app.key)), await c.get(_url(app.key, uuid.uuid4()))):
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "permission_denied"
            assert "admin:usage:read" in str(resp.json()["error"]["details"])


async def test_writes_403_with_read_only(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=["admin:usage:read"]) as c:
        post = await c.post(_url(app.key), json=_body())
        patch = await c.patch(_url(app.key, uuid.uuid4()), json={"default_limit": 1, "reason": "x"})
    for resp in (post, patch):
        assert resp.status_code == 403
        assert "admin:usage:write" in str(resp.json()["error"]["details"])


async def test_admin_wildcard_allows_all(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        assert (await c.get(_url(app.key))).status_code == 200
        assert (await c.get(_url(app.key, m["id"]))).status_code == 200
        resp = await c.patch(_url(app.key, m["id"]), json={"default_limit": 1, "reason": "r"})
        assert resp.status_code == 200


# --- create -----------------------------------------------------------------------


async def test_create_happy_path_201_etag(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        resp = await c.post(_url(app.key), json=_body())
    assert resp.status_code == 201
    body = resp.json()
    assert resp.headers["ETag"].startswith('"')
    assert body["app_key"] == app.key
    assert (body["key"], body["unit"], body["default_limit"]) == ("chat-turns", "count", 20)
    assert (body["period"], body["status"]) == ("week", "active")
    assert "reason" not in body


async def test_create_explicit_period_month(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        body = await _create(c, app.key, period="month", default_limit=0)
    assert (body["period"], body["default_limit"]) == ("month", 0)


async def test_create_duplicate_key_409(make_client: MakeClient, db_session: AsyncSession) -> None:
    a, b = await _app(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        await _create(c, a.key)
        dup = await c.post(_url(a.key), json=_body())
        await _create(c, b.key)  # same key on another app
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "usage_metric_exists"


async def test_create_archived_key_still_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        await c.patch(_url(app.key, m["id"]), json={"status": "archived", "reason": "old"})
        resp = await c.post(_url(app.key), json=_body())
    assert resp.status_code == 409


async def test_create_concurrent_duplicate_409(
    make_client: MakeClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.usage.models import UsageMetric
    from app.modules.usage.repository import UsageMetricRepository

    app = await _app(db_session)
    real = UsageMetricRepository.find_by_key

    async def _miss(self: Any, app_id: uuid.UUID, key: str) -> None:
        # The row a concurrent request committed after our pre-check looked.
        db_session.add(
            UsageMetric(app_id=app_id, key=key, display_name="x", unit="count", default_limit=1)
        )
        await db_session.flush()

    monkeypatch.setattr(UsageMetricRepository, "find_by_key", _miss)
    async with make_client(admin_permissions=SUPER) as c:
        resp = await c.post(_url(app.key), json=_body())
    monkeypatch.setattr(UsageMetricRepository, "find_by_key", real)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "usage_metric_exists"


@pytest.mark.parametrize(
    "change",
    [
        {"key": "Bad Key"},
        {"unit": "1"},
        {"default_limit": -1},
        {"default_limit": "5"},
        {"default_limit": 2**53},
        {"period": "year"},
        {"reason": ""},
        {"status": "archived"},
        {"display_name": ""},
    ],
)
async def test_create_422_cases(
    make_client: MakeClient, db_session: AsyncSession, change: dict[str, Any]
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        resp = await c.post(_url(app.key), json=_body(**change))
    assert resp.status_code == 422


async def test_create_missing_reason_422(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    body = {k: v for k, v in _body().items() if k != "reason"}
    async with make_client(admin_permissions=SUPER) as c:
        assert (await c.post(_url(app.key), json=body)).status_code == 422


# --- app scoping ------------------------------------------------------------------


async def test_unknown_app_404_on_every_route(make_client: MakeClient) -> None:
    mid = uuid.uuid4()
    async with make_client(admin_permissions=SUPER) as c:
        assert (await c.get(_url("nope"))).status_code == 404
        assert (await c.get(_url("nope", mid))).status_code == 404
        assert (await c.post(_url("nope"), json=_body())).status_code == 404
        resp = await c.patch(_url("nope", mid), json={"default_limit": 1, "reason": "x"})
        assert resp.status_code == 404


async def test_admin_app_404_on_every_route(make_client: MakeClient) -> None:
    mid = uuid.uuid4()
    async with make_client(admin_permissions=SUPER) as c:
        assert (await c.get(_url("admin"))).status_code == 404
        assert (await c.get(_url("admin", mid))).status_code == 404
        assert (await c.post(_url("admin"), json=_body())).status_code == 404
        resp = await c.patch(_url("admin", mid), json={"default_limit": 1, "reason": "x"})
        assert resp.status_code == 404


async def test_disabled_app_allowed(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session, status="disabled")
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        resp = await c.patch(_url(app.key, m["id"]), json={"default_limit": 3, "reason": "x"})
    assert resp.status_code == 200


# --- list / detail ----------------------------------------------------------------


async def test_list_pagination_order_and_bounds(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        for key in ("zz", "aa", "mm"):
            await _create(c, app.key, key=key)
        page = (await c.get(_url(app.key), params={"limit": 2})).json()
        rest = (await c.get(_url(app.key), params={"limit": 2, "offset": 2})).json()
        too_big = await c.get(_url(app.key), params={"limit": 101})
    assert (page["total"], [i["key"] for i in page["items"]]) == (3, ["aa", "mm"])
    assert [i["key"] for i in rest["items"]] == ["zz"]
    assert too_big.status_code == 422


async def test_list_status_filter(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        await _create(c, app.key, key="keep")
        gone = await _create(c, app.key, key="gone")
        await c.patch(_url(app.key, gone["id"]), json={"status": "archived", "reason": "x"})

        async def keys(**params: str) -> list[str]:
            return [i["key"] for i in (await c.get(_url(app.key), params=params)).json()["items"]]

        assert await keys() == ["gone", "keep"]
        assert await keys(status="active") == ["keep"]
        assert await keys(status="archived") == ["gone"]
        assert (await c.get(_url(app.key), params={"status": "x"})).status_code == 422


async def test_detail_other_app_metric_404(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    a, b = await _app(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, a.key)
        assert (await c.get(_url(b.key, m["id"]))).status_code == 404
        resp = await c.patch(_url(b.key, m["id"]), json={"default_limit": 1, "reason": "x"})
        assert resp.status_code == 404


async def test_detail_unknown_metric_404(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        assert (await c.get(_url(app.key, uuid.uuid4()))).status_code == 404
        assert (await c.get(_url(app.key, "not-a-uuid"))).status_code == 422


# --- patch ------------------------------------------------------------------------


async def test_patch_display_name_limit_period(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        got = await c.get(_url(app.key, m["id"]))
        resp = await c.patch(
            _url(app.key, m["id"]),
            json={"display_name": "Turns", "default_limit": 7, "period": "day", "reason": "tune"},
        )
    body = resp.json()
    assert resp.status_code == 200
    assert (body["display_name"], body["default_limit"], body["period"]) == ("Turns", 7, "day")
    assert (body["key"], body["unit"], body["created_at"]) == (m["key"], m["unit"], m["created_at"])
    assert resp.headers["ETag"] != got.headers["ETag"]


@pytest.mark.parametrize("field", ["key", "unit"])
async def test_patch_key_or_unit_422(
    make_client: MakeClient, db_session: AsyncSession, field: str
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        resp = await c.patch(_url(app.key, m["id"]), json={field: "other", "reason": "x"})
    assert resp.status_code == 422


async def test_patch_422_cases(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        for body in (
            {"reason": "x"},
            {"default_limit": 1},
            {"default_limit": None, "reason": "x"},
            {"period": "year", "reason": "x"},
        ):
            resp = await c.patch(_url(app.key, m["id"]), json=body)
            assert resp.status_code == 422, body


async def test_patch_archive_and_restore(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        arch = await c.patch(_url(app.key, m["id"]), json={"status": "archived", "reason": "x"})
        assert arch.json()["status"] == "archived"
        assert (await c.get(_url(app.key, m["id"]))).json()["status"] == "archived"
        back = await c.patch(_url(app.key, m["id"]), json={"status": "active", "reason": "y"})
        assert back.json()["status"] == "active"


async def test_patch_if_match_stale_412(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        stale = (await c.get(_url(app.key, m["id"]))).headers["ETag"]
        await c.patch(_url(app.key, m["id"]), json={"default_limit": 1, "reason": "x"})
        resp = await c.patch(
            _url(app.key, m["id"]),
            json={"default_limit": 2, "reason": "x"},
            headers={"If-Match": stale},
        )
        current = (await c.get(_url(app.key, m["id"]))).json()
    assert resp.status_code == 412
    assert resp.json()["error"]["code"] == "precondition_failed"
    assert current["default_limit"] == 1


async def test_patch_if_match_current_ok(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        etag = (await c.get(_url(app.key, m["id"]))).headers["ETag"]
        resp = await c.patch(
            _url(app.key, m["id"]),
            json={"default_limit": 2, "reason": "x"},
            headers={"If-Match": etag},
        )
    assert resp.status_code == 200


async def test_patch_if_match_star_ok(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        resp = await c.patch(
            _url(app.key, m["id"]),
            json={"default_limit": 2, "reason": "x"},
            headers={"If-Match": "*"},
        )
    assert resp.status_code == 200


async def test_noop_patch_writes_nothing(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        before = len(await _audit(db_session))
        resp = await c.patch(
            _url(app.key, m["id"]), json={"default_limit": 20, "period": "week", "reason": "x"}
        )
    assert resp.status_code == 200
    assert resp.json()["updated_at"] == m["updated_at"]
    assert len(await _audit(db_session)) == before == 1


# --- audit ------------------------------------------------------------------------


async def test_create_writes_audit_row_with_reason(
    make_client: MakeClient, db_session: AsyncSession, staff: User
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=staff) as c:
        m = await _create(c, app.key, reason="launch plan")
    (row,) = await _audit(db_session)
    assert row.action == "usage_metric.created"
    assert (str(row.target_id), row.app_key, row.actor_user_id) == (m["id"], app.key, staff.id)
    assert row.changes["reason"] == "launch plan"
    assert row.changes["default_limit"] == {"from": None, "to": 20}
    assert row.changes["period"] == {"from": None, "to": "week"}


async def test_patch_writes_updated_row(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        await c.patch(
            _url(app.key, m["id"]),
            json={"default_limit": 50, "period": "month", "reason": "raise"},
        )
    (row,) = [r for r in await _audit(db_session) if r.action == "usage_metric.updated"]
    assert row.changes == {
        "default_limit": {"from": 20, "to": 50},
        "period": {"from": "week", "to": "month"},
        "reason": "raise",
    }


async def test_archive_and_restore_rows(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key)
        await c.patch(_url(app.key, m["id"]), json={"status": "archived", "reason": "r1"})
        await c.patch(_url(app.key, m["id"]), json={"status": "active", "reason": "r2"})
        await c.patch(
            _url(app.key, m["id"]),
            json={"status": "archived", "default_limit": 1, "reason": "r3"},
        )
    # Rows of one transaction share `occurred_at`, so compare as a set of (action, reason).
    rows = [r for r in await _audit(db_session) if r.action != "usage_metric.created"]
    assert sorted((r.action, r.changes["reason"]) for r in rows) == [
        ("usage_metric.archived", "r1"),
        ("usage_metric.archived", "r3"),
        ("usage_metric.restored", "r2"),
        ("usage_metric.updated", "r3"),
    ]
    first = next(r for r in rows if r.changes["reason"] == "r1")
    assert first.changes["status"] == {"from": "active", "to": "archived"}


async def test_reason_not_in_logs(
    make_client: MakeClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    app = await _app(db_session)
    caplog.set_level(logging.DEBUG)
    async with make_client(admin_permissions=SUPER) as c:
        m = await _create(c, app.key, reason="sekret-reason-1")
        await c.patch(
            _url(app.key, m["id"]), json={"default_limit": 1, "reason": "sekret-reason-2"}
        )
    assert "sekret-reason" not in caplog.text


async def test_app_delete_with_metrics_is_409_not_500(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER) as c:
        await _create(c, app.key)
        resp = await c.delete(f"{V1}/apps/{app.key}")
    assert resp.status_code == 409


async def test_openapi_if_match_etag_declared(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=SUPER) as c:
        paths = (await c.get("/openapi.json")).json()["paths"]
    detail = paths["/api/v1/apps/{key}/usage-metrics/{metric_id}"]
    assert "ETag" in detail["get"]["responses"]["200"]["headers"]
    assert "412" in detail["patch"]["responses"]
    assert "ETag" in detail["patch"]["responses"]["200"]["headers"]
    assert any(p["name"] == "If-Match" for p in detail["patch"]["parameters"])
    assert (
        "ETag" in paths["/api/v1/apps/{key}/usage-metrics"]["post"]["responses"]["201"]["headers"]
    )
