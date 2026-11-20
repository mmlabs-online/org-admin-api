"""ADM-029: staff list, detail, mark done / reopen / note on /api/v1/bug-reports."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.bug_reports.models import BugReport, BugReportAttachment
from app.modules.rbac.models import App
from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.rbac_factories import make_app, make_user

URL = "/api/v1/bug-reports"
READ, WRITE = "admin:bug-reports:read", "admin:bug-reports:write"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _key() -> str:
    return f"app-{uuid.uuid4().hex[:8]}"


async def _report(
    db: AsyncSession, app: App, user: User, *, at: datetime | None = None, **kwargs: Any
) -> BugReport:
    fields: dict[str, Any] = {"description": "it broke", **kwargs}
    if at is not None:
        fields["created_at"] = at
    report = BugReport(app_id=app.id, user_id=user.id, **fields)
    db.add(report)
    await db.flush()
    await db.refresh(report)
    return report


async def _audit(db: AsyncSession) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.target_type == "bug_report")
        .order_by(AuditLog.action, AuditLog.id)  # same-transaction rows share `occurred_at`
        .execution_options(populate_existing=True)
    )
    return list(rows)


_DEFAULT_STAFF: list[User] = []


@pytest.fixture(autouse=True)
async def _default_staff(db_session: AsyncSession) -> None:
    """A real staff user: `handled_by` is a FK, so a random principal id would fail."""
    _DEFAULT_STAFF[:] = [await make_user(db_session)]


@pytest.fixture
async def seeded(db_session: AsyncSession) -> dict[str, Any]:
    a1, a2 = await make_app(db_session, _key()), await make_app(db_session, _key())
    u1, u2 = await make_user(db_session), await make_user(db_session)
    r1 = await _report(db_session, a1, u1, at=T0)
    r2 = await _report(db_session, a2, u2, at=T0 + timedelta(days=1))
    r3 = await _report(db_session, a1, u2, at=T0 + timedelta(days=2))
    return {"a1": a1, "a2": a2, "u1": u1, "u2": u2, "r": [r1, r2, r3]}


# --- auth -----------------------------------------------------------------------


async def test_routes_401_without_token(make_client: MakeClient) -> None:
    rid = uuid.uuid4()
    async with make_client() as c:
        assert (await c.get(URL)).status_code == 401
        assert (await c.get(f"{URL}/{rid}")).status_code == 401
        assert (await c.patch(f"{URL}/{rid}", json={"status": "done"})).status_code == 401


async def test_read_routes_403_with_unrelated_permission(make_client: MakeClient) -> None:
    rid = uuid.uuid4()
    async with make_client(admin_permissions=["admin:users:read", WRITE]) as c:
        assert (await c.get(URL)).status_code == 403
        assert (await c.get(f"{URL}/{rid}")).status_code == 403


async def test_patch_403_with_read_only(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=[READ]) as c:
        resp = await c.patch(f"{URL}/{uuid.uuid4()}", json={"status": "done"})
    assert resp.status_code == 403


async def test_admin_wildcard_allows_all(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    rid = seeded["r"][0].id
    async with make_client(admin_permissions=["admin:*"], admin_user=_DEFAULT_STAFF[0]) as c:
        assert (await c.get(URL)).status_code == 200
        assert (await c.get(f"{URL}/{rid}")).status_code == 200
        assert (await c.patch(f"{URL}/{rid}", json={"status": "done"})).status_code == 200


# --- list -----------------------------------------------------------------------


async def _list(make_client: MakeClient, **params: Any) -> Any:
    async with make_client(admin_permissions=[READ]) as c:
        return await c.get(URL, params=params)


async def test_list_across_apps_newest_first(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    tie = await _report(db_session, seeded["a2"], seeded["u1"], at=T0 + timedelta(days=2))
    resp = await _list(make_client)
    assert resp.status_code == 200
    body = resp.json()
    r1, r2, r3 = seeded["r"]
    tied = sorted([str(r3.id), str(tie.id)], reverse=True)
    assert [i["id"] for i in body["items"]] == [*tied, str(r2.id), str(r1.id)]
    assert body["total"] == 4
    assert {i["app_key"] for i in body["items"]} == {seeded["a1"].key, seeded["a2"].key}


async def test_list_filter_app_key(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    body = (await _list(make_client, app_key=seeded["a1"].key)).json()
    assert {i["id"] for i in body["items"]} == {str(seeded["r"][0].id), str(seeded["r"][2].id)}


async def test_list_filter_unknown_app_key_empty(make_client: MakeClient) -> None:
    body = (await _list(make_client, app_key="nope-app")).json()
    assert (body["items"], body["total"]) == ([], 0)


async def test_list_filter_status(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    done = await _report(
        db_session, seeded["a1"], seeded["u1"], status="done", handled_at=datetime.now(UTC)
    )
    body = (await _list(make_client, status="done")).json()
    assert [i["id"] for i in body["items"]] == [str(done.id)]
    assert (await _list(make_client, status="open")).json()["total"] == 3


async def test_list_filter_reporter_id(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    body = (await _list(make_client, reporter_id=str(seeded["u2"].id))).json()
    assert {i["id"] for i in body["items"]} == {str(seeded["r"][1].id), str(seeded["r"][2].id)}


async def test_list_filter_date_range(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    resp = await _list(
        make_client,
        **{
            "from": (T0 + timedelta(days=1)).isoformat(),
            "to": (T0 + timedelta(days=2)).isoformat(),
        },
    )
    assert [i["id"] for i in resp.json()["items"]] == [str(seeded["r"][1].id)]  # to is exclusive


async def test_list_from_after_to_422(make_client: MakeClient) -> None:
    resp = await _list(
        make_client, **{"from": (T0 + timedelta(days=1)).isoformat(), "to": T0.isoformat()}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == [{"field": "from", "reason": "after_to"}]


async def test_list_naive_datetime_422(make_client: MakeClient) -> None:
    assert (await _list(make_client, **{"from": "2026-01-01T00:00:00"})).status_code == 422


async def test_list_invalid_status_422(make_client: MakeClient) -> None:
    assert (await _list(make_client, status="closed")).status_code == 422


async def test_list_paging_bounds(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    assert (await _list(make_client, limit=0)).status_code == 422
    assert (await _list(make_client, limit=101)).status_code == 422
    body = (await _list(make_client, limit=1, offset=1)).json()
    assert (len(body["items"]), body["total"], body["limit"], body["offset"]) == (1, 3, 1, 1)
    assert body["items"][0]["id"] == str(seeded["r"][1].id)


async def test_list_item_shape(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    seeded["u1"].display_name = "Una"
    await _report(
        db_session, seeded["a1"], seeded["u1"], at=T0 + timedelta(days=9), description="é" * 300
    )
    item = (await _list(make_client, limit=1)).json()["items"][0]
    assert set(item) == {
        "id", "app_key", "org_id", "status", "reporter", "description_preview", "handler",
        "handled_at", "created_at", "updated_at",
    }  # fmt: skip
    assert item["reporter"] == {
        "id": str(seeded["u1"].id),
        "display_name": "Una",
        "email": seeded["u1"].email,
    }
    assert item["description_preview"] == "é" * 200
    assert item["handler"] is None


# --- detail ---------------------------------------------------------------------


async def test_detail_shape(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    handler = await make_user(db_session)
    handler.display_name = "Hana"
    report = await _report(
        db_session, seeded["a1"], seeded["u1"], page_url="https://x.test/p", app_version="1.0",
        user_agent="UA", resolution_note="n", status="done", handled_by=handler.id,
        handled_at=datetime.now(UTC),
    )  # fmt: skip
    async with make_client(admin_permissions=[READ]) as c:
        resp = await c.get(f"{URL}/{report.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reporter"]["email"] == seeded["u1"].email
    assert body["handler"] == {"id": str(handler.id), "display_name": "Hana"}
    assert (body["description"], body["page_url"], body["resolution_note"]) == (
        "it broke", "https://x.test/p", "n",
    )  # fmt: skip
    assert body["attachments"] == []
    assert "description_preview" not in body
    assert resp.headers["etag"].startswith('"')


async def test_detail_lists_attachments(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    report = await _report(db_session, seeded["a1"], seeded["u1"])
    other = await _report(db_session, seeded["a1"], seeded["u2"])
    ids = [uuid.uuid4() for _ in range(3)]
    # Inserted out of order; the listing is oldest first (ties: id). Another report's is not shown.
    for i, at in ((2, T0 + timedelta(minutes=2)), (0, T0), (1, T0 + timedelta(minutes=1))):
        db_session.add(
            BugReportAttachment(
                id=ids[i], report_id=report.id, drive_file_id=f"SENTINELdriveID{i}",
                content_type="image/png", size_bytes=100 + i, created_at=at,
            )
        )  # fmt: skip
    db_session.add(
        BugReportAttachment(
            report_id=other.id, drive_file_id="SENTINELdriveIDother",
            content_type="image/gif", size_bytes=50,
        )
    )  # fmt: skip
    await db_session.flush()
    async with make_client(admin_permissions=[READ, WRITE]) as c:
        resp = await c.get(f"{URL}/{report.id}")
        patched = await c.patch(f"{URL}/{report.id}", json={"resolution_note": "n"})
    assert resp.status_code == 200
    items = resp.json()["attachments"]
    assert [a["id"] for a in items] == [str(i) for i in ids]
    assert [(a["content_type"], a["size_bytes"]) for a in items] == [
        ("image/png", 100), ("image/png", 101), ("image/png", 102),
    ]  # fmt: skip
    assert all(set(a) == {"id", "content_type", "size_bytes", "created_at"} for a in items)
    assert "SENTINELdriveID" not in resp.text
    assert patched.status_code == 200
    assert len(patched.json()["attachments"]) == 3


async def test_detail_done_with_deleted_handler(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    report = await _report(
        db_session, seeded["a1"], seeded["u1"], status="done", handled_at=datetime.now(UTC)
    )
    async with make_client(admin_permissions=[READ]) as c:
        body = (await c.get(f"{URL}/{report.id}")).json()
    assert (body["status"], body["handler"]) == ("done", None)


async def test_get_unknown_404(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=[READ]) as c:
        resp = await c.get(f"{URL}/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_list_and_detail_no_store(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    async with make_client(admin_permissions=[READ]) as c:
        assert (await c.get(URL)).headers["cache-control"] == "no-store"
        resp = await c.get(f"{URL}/{seeded['r'][0].id}")
    assert resp.headers["cache-control"] == "no-store"


async def test_metacharacters_round_trip(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    nasty = "<script>\"'&</script>‮"
    report = await _report(
        db_session, seeded["a1"], seeded["u1"], at=T0 + timedelta(days=9), description=nasty,
        page_url="https://x.test/<a>\"'&", app_version="1<2>&\"'", user_agent=nasty,
    )  # fmt: skip
    async with make_client(admin_permissions=[READ]) as c:
        detail = (await c.get(f"{URL}/{report.id}")).json()
        listed = (await c.get(URL, params={"limit": 1})).json()["items"][0]
    assert detail["description"] == nasty
    assert detail["page_url"] == "https://x.test/<a>\"'&"
    assert detail["app_version"] == "1<2>&\"'"
    assert detail["user_agent"] == nasty
    assert listed["description_preview"] == nasty


# --- PATCH ----------------------------------------------------------------------


async def _patch(
    make_client: MakeClient,
    rid: Any,
    body: Any,
    user: User | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    async with make_client(admin_permissions=[WRITE], admin_user=user or _DEFAULT_STAFF[0]) as c:
        return await c.patch(f"{URL}/{rid}", json=body, headers=headers)


async def test_mark_done_sets_handler_and_time(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    staff = await make_user(db_session)
    report = seeded["r"][0]
    resp = await _patch(make_client, report.id, {"status": "done"}, staff)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["handler"]["id"] == str(staff.id)
    assert body["handled_at"] is not None
    await db_session.refresh(report)
    assert (report.handled_by, report.handled_at is not None) == (staff.id, True)
    assert resp.headers["cache-control"] == "no-store"


async def test_reopen_clears_handler_and_time_keeps_note(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    staff = await make_user(db_session)
    rid = seeded["r"][0].id
    await _patch(make_client, rid, {"status": "done", "resolution_note": "fixed"}, staff)
    resp = await _patch(make_client, rid, {"status": "open"}, staff)
    body = resp.json()
    assert (body["status"], body["handler"], body["handled_at"]) == ("open", None, None)
    assert body["resolution_note"] == "fixed"
    assert [r.action for r in await _audit(db_session)] == [
        "bug_report.marked_done",
        "bug_report.note_updated",
        "bug_report.reopened",
    ]


async def test_done_again_keeps_original_handler(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    first, second = await make_user(db_session), await make_user(db_session)
    rid = seeded["r"][0].id
    one = await _patch(make_client, rid, {"status": "done"}, first)
    two = await _patch(make_client, rid, {"status": "done"}, second)
    assert two.status_code == 200
    assert two.json()["handler"]["id"] == str(first.id)
    assert two.json()["handled_at"] == one.json()["handled_at"]
    assert two.headers["etag"] == one.headers["etag"]
    assert len(await _audit(db_session)) == 1


async def test_note_on_open_report(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    resp = await _patch(make_client, seeded["r"][0].id, {"resolution_note": " looking "})
    body = resp.json()
    assert (body["status"], body["resolution_note"], body["handler"]) == ("open", "looking", None)


async def test_note_clear_with_null(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    rid = seeded["r"][0].id
    await _patch(make_client, rid, {"resolution_note": "x"})
    resp = await _patch(make_client, rid, {"resolution_note": None})
    assert resp.json()["resolution_note"] is None
    changes = [r.changes for r in await _audit(db_session)]
    assert {"resolution_note": {"changed": True, "cleared": True}} in changes


async def test_patch_bumps_etag(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    rid = seeded["r"][0].id
    async with make_client(admin_permissions=[READ]) as c:
        before = (await c.get(f"{URL}/{rid}")).headers["etag"]
    resp = await _patch(make_client, rid, {"status": "done"})
    assert resp.headers["etag"] != before


async def test_noop_writes_nothing(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    rid = seeded["r"][0].id
    first = await _patch(make_client, rid, {"resolution_note": "n"})
    before = len(await _audit(db_session))
    for body in (
        {"status": "open"},
        {"resolution_note": "n"},
        {"status": "open", "resolution_note": "n"},
    ):
        resp = await _patch(make_client, rid, body)
        assert resp.status_code == 200
        assert resp.headers["etag"] == first.headers["etag"]
    assert len(await _audit(db_session)) == before
    other = seeded["r"][1].id
    assert (await _patch(make_client, other, {"resolution_note": None})).status_code == 200


async def test_audit_rows_ids_and_status_only(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    staff = await make_user(db_session)
    report = await _report(db_session, seeded["a1"], seeded["u1"], description="SENTINEL-DESC")
    await _patch(
        make_client, report.id, {"status": "done", "resolution_note": "SENTINEL-NOTE"}, staff
    )
    rows = await _audit(db_session)
    assert [r.action for r in rows] == ["bug_report.marked_done", "bug_report.note_updated"]
    for row in rows:
        assert row.target_id == report.id
        assert row.actor_user_id == staff.id
        assert row.app_key == seeded["a1"].key
        assert row.org_id is None
        assert "SENTINEL" not in repr(row.changes)
    assert rows[0].changes == {"status": {"from": "open", "to": "done"}}
    assert rows[1].changes == {"resolution_note": {"changed": True, "cleared": False}}


async def test_status_and_note_in_one_patch_writes_two_rows(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    await _patch(make_client, seeded["r"][0].id, {"status": "done", "resolution_note": "n"})
    assert len(await _audit(db_session)) == 2


async def test_patch_response_no_store(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    resp = await _patch(make_client, seeded["r"][0].id, {"status": "done"})
    assert resp.headers["cache-control"] == "no-store"


async def test_patch_unknown_404(make_client: MakeClient) -> None:
    assert (await _patch(make_client, uuid.uuid4(), {"status": "done"})).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"status": "closed"},
        {"status": None},
        {},
        {"status": "done", "handled_by": str(uuid.uuid4())},
        {"resolution_note": ""},
        {"resolution_note": "   "},
        {"resolution_note": "x" * 5001},
    ],
)
async def test_patch_invalid_422(
    make_client: MakeClient, seeded: dict[str, Any], body: dict[str, Any]
) -> None:
    resp = await _patch(make_client, seeded["r"][0].id, body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_patch_stale_if_match_412(
    make_client: MakeClient, db_session: AsyncSession, seeded: dict[str, Any]
) -> None:
    rid = seeded["r"][0].id
    stale = '"1"'
    resp = await _patch(make_client, rid, {"status": "done"}, headers={"If-Match": stale})
    assert resp.status_code == 412
    assert resp.json()["error"]["code"] == "precondition_failed"
    assert resp.headers["etag"].startswith('"')
    assert await _audit(db_session) == []
    await db_session.refresh(seeded["r"][0])
    assert seeded["r"][0].status == "open"


async def test_patch_stale_if_match_on_noop_412(
    make_client: MakeClient, seeded: dict[str, Any]
) -> None:
    resp = await _patch(
        make_client, seeded["r"][0].id, {"status": "open"}, headers={"If-Match": '"1"'}
    )
    assert resp.status_code == 412


async def test_patch_if_match_current_200(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    rid = seeded["r"][0].id
    async with make_client(admin_permissions=[READ]) as c:
        etag = (await c.get(f"{URL}/{rid}")).headers["etag"]
    resp = await _patch(make_client, rid, {"status": "done"}, headers={"If-Match": etag})
    assert resp.status_code == 200


async def test_patch_if_match_star_200(make_client: MakeClient, seeded: dict[str, Any]) -> None:
    resp = await _patch(
        make_client, seeded["r"][0].id, {"status": "done"}, headers={"If-Match": "*"}
    )
    assert resp.status_code == 200
