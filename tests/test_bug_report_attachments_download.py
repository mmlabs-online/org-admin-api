"""GET /api/v1/bug-reports/{id}/attachments/{aid}/content (ADM-030, ADR-0023): the staff download,
proxied from the fake Drive with fixed safe headers."""

import uuid
from typing import Any

import pytest
from httpx import Response
from sqlalchemy import func, select, text

from app.modules.attachments.errors import DriveError
from app.modules.attachments.store import DisabledAttachmentStore
from app.modules.audit.models import AuditLog
from app.modules.bug_reports.models import BugReport
from app.modules.bug_reports.staff_service import BugReportStaffService
from tests.attachment_world import PNG, PNG_TYPE, MakeWorld, World

pytestmark = pytest.mark.usefixtures("_quiet_drive")


def _url(report_id: uuid.UUID | str, attachment_id: uuid.UUID | str) -> str:
    return f"/api/v1/bug-reports/{report_id}/attachments/{attachment_id}/content"


async def _upload(w: World, data: bytes = PNG) -> str:
    resp = await w.upload(data)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _error(resp: Response) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


async def test_download_headers(make_world: MakeWorld) -> None:
    async with make_world() as w:
        aid = await _upload(w)
        resp = await w.client.get(_url(w.report_id, aid))
        assert resp.status_code == 200
        assert resp.content == PNG
        assert resp.headers["content-type"] == PNG_TYPE
        assert resp.headers["content-length"] == str(len(PNG))
        assert resp.headers["content-disposition"] == f'attachment; filename="{aid}.png"'
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["cross-origin-resource-policy"] == "same-origin"


async def test_download_not_found_404_is_one_answer(make_world: MakeWorld, db_session: Any) -> None:
    async with make_world() as w:
        aid = await _upload(w)
        other = BugReport(app_id=w.rbac_app.id, user_id=w.user_id, description="other")
        db_session.add(other)
        await db_session.flush()
        other_id = other.id
        await db_session.commit()
        cases = {
            "unknown_report": await w.client.get(_url(uuid.uuid4(), aid)),
            "unknown_attachment": await w.client.get(_url(w.report_id, uuid.uuid4())),
            "attachment_of_other_report": await w.client.get(_url(other_id, aid)),
        }
        for name, resp in cases.items():
            assert resp.status_code == 404, name
            assert _error(resp)["code"] == "not_found", name
        assert len({r.text for r in cases.values()}) == 1


async def _make_unavailable(w: World, how: str, aid: str) -> None:
    if how == "drive_not_found":
        for fid in w.drive_attachments():
            del w.fake.files[fid]
    elif how == "deleted_marker":
        await w.db.execute(text("UPDATE bug_report_attachments SET file_deleted_at = now()"))
        await w.db.commit()
    else:
        w.app.state.attachment_store = DisabledAttachmentStore()


@pytest.mark.parametrize("how", ["drive_not_found", "deleted_marker", "drive_disabled"])
async def test_download_unavailable_404(make_world: MakeWorld, how: str) -> None:
    async with make_world() as w:
        aid = await _upload(w)
        await _make_unavailable(w, how, aid)
        resp = await w.client.get(_url(w.report_id, aid))
        assert resp.status_code == 404
        assert _error(resp) == {
            "code": "attachment_unavailable",
            "message": "Attachment is unavailable",
            "details": None,
        }


async def test_download_drive_transient_503(make_world: MakeWorld) -> None:
    async with make_world() as w:
        aid = await _upload(w)
        w.fake.queue("read", 503, 503, 503)
        resp = await w.client.get(_url(w.report_id, aid))
        assert resp.status_code == 503
        assert _error(resp)["code"] == "attachment_storage_unavailable"
        assert _error(resp)["details"] == {"retryable": True}
        assert resp.headers["retry-after"] == "30"
        assert "content-disposition" not in resp.headers  # no partial 200


async def test_download_truncated_stream_aborts(make_world: MakeWorld) -> None:
    async with make_world() as w:
        aid = await _upload(w)
        w.fake.read_chunks = [PNG[:5]]
        download = await BugReportStaffService(w.db).open_download(
            w.report_id, uuid.UUID(aid), w.app.state.attachment_store
        )
        with pytest.raises(DriveError):
            async for _ in download.body:
                pass
        assert w.fake.streams_closed == 1


async def test_download_closes_drive_stream(make_world: MakeWorld) -> None:
    async with make_world() as w:
        aid = await _upload(w)
        before = w.fake.streams_closed
        assert (await w.client.get(_url(w.report_id, aid))).status_code == 200
        assert w.fake.streams_closed == before + 1
        w.fake.read_chunks = [PNG[:10], PNG[10:]]
        download = await BugReportStaffService(w.db).open_download(
            w.report_id, uuid.UUID(aid), w.app.state.attachment_store
        )
        assert await anext(download.body) == PNG[:10]
        await download.body.aclose()  # early client close
        assert w.fake.streams_closed == before + 2


@pytest.mark.parametrize(("permissions", "status"), [(None, 401), (("admin:users:read",), 403)])
async def test_download_requires_permission_401_403(
    make_world: MakeWorld, permissions: tuple[str, ...] | None, status: int
) -> None:
    async with make_world(admin_permissions=permissions) as w:
        aid = str(uuid.uuid4())  # the permission check comes first: no upload needed
        assert (await w.client.get(_url(w.report_id, aid))).status_code == status


async def test_download_not_audited(make_world: MakeWorld) -> None:
    async with make_world() as w:
        aid = await _upload(w)
        before = await w.db.scalar(select(func.count()).select_from(AuditLog))
        assert (await w.client.get(_url(w.report_id, aid))).status_code == 200
        assert await w.db.scalar(select(func.count()).select_from(AuditLog)) == before
