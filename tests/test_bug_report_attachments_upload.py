"""POST /api/v1/auth/bug-reports/{id}/attachments (ADM-030, ADR-0023): the upload, against the
in-memory fake Drive (never real Google). Real-commit race: `test_concurrent_uploads_respect_cap`.
"""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import SessionLocal
from app.main import create_app
from app.modules.attachments.drive import GoogleDriveStore
from app.modules.attachments.store import (
    MAX_ATTACHMENT_BYTES,
    AttachmentStore,
    DisabledAttachmentStore,
)
from app.modules.audit.models import AuditLog
from app.modules.audit.service import AuditLogger
from app.modules.bug_reports.models import BugReport, BugReportAttachment
from app.modules.bug_reports.repository import BugReportAttachmentRepository
from app.modules.rbac.models import Role, RoleAssignment
from tests.attachment_world import IMAGES, PNG, PNG_TYPE, SENTINEL, MakeWorld, World
from tests.auth_factories import (
    ORIGIN,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    parse_set_cookie,
    shift,
)
from tests.fake_drive import FakeDrive, drive_settings, error_body
from tests.test_bug_reports_submit import OTHER_ORIGIN, _app, _member
from tests.test_login_concurrency import TIMEOUT, _cleanup

pytestmark = pytest.mark.usefixtures("_quiet_drive")
DENIED = "attachment_upload_denied"


def _error(resp: Response) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


# --- happy path ----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(IMAGES))
async def test_upload_each_type_roundtrip(make_world: MakeWorld, kind: str) -> None:
    ctype, data = IMAGES[kind]
    async with make_world() as w:
        resp = await w.upload(data, ctype)
        assert resp.status_code == 201, resp.text
        assert resp.headers["cache-control"] == "no-store"
        body = resp.json()
        assert set(body) == {"id", "content_type", "size_bytes", "created_at"}
        assert (body["content_type"], body["size_bytes"]) == (ctype, len(data))
        (row,) = await w.rows()
        assert (str(row.id), row.content_type, row.size_bytes) == (body["id"], ctype, len(data))
        # Staff download: byte-identical.
        got = await w.client.get(
            f"/api/v1/bug-reports/{w.report_id}/attachments/{body['id']}/content"
        )
        assert got.status_code == 200
        assert hashlib.sha256(got.content).digest() == hashlib.sha256(data).digest()
        # Drive: named by the attachment id, in the app's folder.
        (file_id,) = w.drive_attachments()
        assert w.fake.files[file_id]["name"] == body["id"]
        audit = (
            await w.db.scalars(
                select(AuditLog).where(AuditLog.action == "bug_report.attachment_added")
            )
        ).all()
        assert len(audit) == 1
        assert audit[0].changes == {"attachment_id": body["id"], "attachment_count": 1}
        assert audit[0].outcome == "success"
        assert audit[0].actor_user_id == w.user_id
        assert (audit[0].target_type, audit[0].target_id) == ("bug_report", w.report_id)


async def test_upload_ignores_client_filename(make_world: MakeWorld) -> None:
    async with make_world() as w:
        resp = await w.upload(headers={"Content-Disposition": 'attachment; filename="evil.html"'})
        assert resp.status_code == 201
        (file_id,) = w.drive_attachments()
        assert "evil" not in str(w.fake.files[file_id])


# --- type, length --------------------------------------------------------------------------------

REJECTED: dict[str, tuple[bytes, str | None]] = {
    "svg": (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml"),
    "html": (b"<!DOCTYPE html><html></html>", "text/html"),
    "pdf": (b"%PDF-1.7 plus some more bytes", "application/pdf"),
    "jpg_alias": (IMAGES["jpeg"][1], "image/jpg"),
    "missing": (PNG, None),
    "mismatch_png_as_jpeg": (PNG, "image/jpeg"),
    "html_as_png": (b"<html><body>hello</body></html>", PNG_TYPE),
    "too_short": (PNG[:8] + b"xx", PNG_TYPE),
}


@pytest.mark.parametrize("case", list(REJECTED))
async def test_upload_rejects_type(make_world: MakeWorld, case: str) -> None:
    data, ctype = REJECTED[case]
    async with make_world() as w:
        resp = await w.upload(data, ctype)
        assert resp.status_code == 415, resp.text
        assert _error(resp) == {
            "code": "unsupported_media_type",
            "message": "Unsupported media type",
            "details": None,
        }
        assert w.fake.count("create") == 0
        assert await w.rows() == []


class BodyCounter:
    """ASGI wrapper that counts the request body messages the application pulls."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.body_messages = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def counting() -> Any:
            message = await receive()
            if message["type"] == "http.request" and message.get("body"):
                self.body_messages += 1
            return message

        await self.app(scope, counting, send)


async def test_upload_413_before_body_read(make_world: MakeWorld) -> None:
    async with make_world() as w:
        counter = BodyCounter(w.app)
        transport = ASGITransport(app=counter, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await w.upload(PNG + b"\x00" * MAX_ATTACHMENT_BYTES, client=client)
        assert resp.status_code == 413, resp.text
        assert _error(resp)["code"] == "payload_too_large"
        assert counter.body_messages == 0
        assert w.fake.count("create") == 0


async def test_upload_413_lying_length(make_world: MakeWorld) -> None:
    async with make_world() as w:
        resp = await w.upload(PNG + b"\x00" * 100, headers={"Content-Length": str(len(PNG))})
        assert resp.status_code == 413
        assert _error(resp)["code"] == "payload_too_large"
        assert w.fake.count("create") == 0


async def test_upload_incomplete_422(make_world: MakeWorld) -> None:
    async with make_world() as w:
        resp = await w.upload(PNG, headers={"Content-Length": str(len(PNG) + 50)})
        assert resp.status_code == 422
        assert _error(resp)["code"] == "attachment_incomplete"
        assert w.fake.count("create") == 0
        assert await w.rows() == []


async def test_upload_411_chunked(make_world: MakeWorld) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield PNG

    async with make_world() as w:
        resp = await w.client.post(
            w.url(),
            params={"account_id": str(w.account_id)},
            content=body(),
            headers={**auth_headers("ricotdin", ORIGIN, w.raw), "Content-Type": PNG_TYPE},
        )
        assert resp.status_code == 411, resp.text
        assert _error(resp)["code"] == "length_required"
        assert w.fake.count("create") == 0


# --- cap -----------------------------------------------------------------------------------------


async def test_upload_sixth_is_409(make_world: MakeWorld) -> None:
    async with make_world() as w:
        w.seed_attachments(4)
        await w.db.commit()
        assert (await w.upload()).status_code == 201
        resp = await w.upload()
        assert resp.status_code == 409
        assert _error(resp)["code"] == "attachment_limit_reached"
        assert _error(resp)["details"] == {"limit": 5}
        assert len(await w.rows()) == 5
        assert w.fake.count("create") == 1


class BarrierStore:
    """Both uploads must reach Drive before either writes its row (forces the race)."""

    configured = True

    def __init__(self, inner: AttachmentStore, parties: int) -> None:
        self.inner = inner
        self.barrier = asyncio.Barrier(parties)

    async def create(self, **kwargs: Any) -> str:
        await asyncio.wait_for(self.barrier.wait(), timeout=TIMEOUT)
        return await self.inner.create(**kwargs)

    async def delete(self, file_id: str) -> None:
        await self.inner.delete(file_id)


async def test_concurrent_uploads_respect_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real commits. Count 4, two parallel uploads: one 201, one 409; the loser's Drive file is
    deleted again; five rows. The audit write is stubbed (the audit table is append-only)."""

    async def no_audit(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(AuditLogger, "record", no_audit)
    app_key = "ucap-" + uuid.uuid4().hex[:8]
    async with SessionLocal() as session:
        rbac_app = await make_login_app(session, key=app_key)
        role = Role(app_id=rbac_app.id, key=f"{app_key}:member", name="Member")
        session.add(role)
        await session.flush()
        user = await make_login_user(session)
        session.add(RoleAssignment(user_id=user.id, role_id=role.id))
        cookie, _, (account,) = await make_device(session, user)
        report = BugReport(app_id=rbac_app.id, user_id=user.id, description="race")
        session.add(report)
        await session.flush()
        for i in range(4):
            session.add(
                BugReportAttachment(
                    report_id=report.id,
                    drive_file_id=f"race{i}{uuid.uuid4().hex}",
                    content_type=PNG_TYPE,
                    size_bytes=len(PNG),
                )
            )
        await session.commit()
        report_id, account_id, app_id = report.id, account.id, rbac_app.id

    fake = FakeDrive()
    store = GoogleDriveStore(drive_settings(), fake.transport)
    app = create_app(Settings(bug_report_attachment_user_rate_limit=1000))
    app.state.attachment_store = BarrierStore(store, 2)

    async def one() -> Response:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                f"/api/v1/auth/bug-reports/{report_id}/attachments",
                params={"account_id": str(account_id)},
                content=PNG,
                headers={**auth_headers(app_key, ORIGIN, cookie), "Content-Type": PNG_TYPE},
            )

    try:
        responses = await asyncio.wait_for(asyncio.gather(one(), one()), timeout=TIMEOUT)
        assert sorted(r.status_code for r in responses) == [201, 409]
        async with SessionLocal() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(BugReportAttachment)
                .where(BugReportAttachment.report_id == report_id)
            )
        assert total == 5
        assert fake.count("delete") == 1
        kinds = [f.get("appProperties", {}).get("kind") for f in fake.files.values()]
        assert kinds.count("attachment") == 1
    finally:
        await store.aclose()
        async with SessionLocal() as session:
            await session.execute(
                delete(BugReportAttachment).where(BugReportAttachment.report_id == report_id)
            )
            await session.execute(delete(BugReport).where(BugReport.user_id == user.id))
            await session.execute(delete(RoleAssignment).where(RoleAssignment.user_id == user.id))
            await session.execute(delete(Role).where(Role.app_id == app_id))
            await _cleanup(session, user.id, app_key, b"")


# --- who may upload ------------------------------------------------------------------------------


async def test_upload_denied_403_is_one_answer(
    make_world: MakeWorld, db_session: AsyncSession
) -> None:
    async with make_world() as w:
        _, other_role = await _app(db_session, "calechip", origins=[OTHER_ORIGIN])
        _, stranger_raw, stranger_account = await _member(db_session, other_role)
        mate_role = await db_session.scalar(select(Role).where(Role.key == "ricotdin:member"))
        _, mate_raw, mate_account = await _member(db_session, mate_role)
        stranger_id, mate_id = stranger_account.id, mate_account.id
        await db_session.commit()
        cases = {
            "other_user_same_app": await w.upload(raw=mate_raw, account_id=mate_id),
            "other_app": await w.upload(
                raw=stranger_raw,
                account_id=stranger_id,
                app_key="calechip",
                origin=OTHER_ORIGIN,
            ),
            "reporter_via_other_app": await w.upload(app_key="calechip", origin=OTHER_ORIGIN),
            "unknown_report": await w.upload(report_id=uuid.uuid4()),
        }
        for name, resp in cases.items():
            assert resp.status_code == 403, (name, resp.text)
            assert _error(resp)["code"] == DENIED, name
        bodies = {resp.text for resp in cases.values()}
        assert len(bodies) == 1  # byte-identical: no existence oracle
        assert w.fake.count("create") == 0


@pytest.mark.parametrize("reason", ["report_done", "window_expired"])
async def test_upload_closed_403(make_world: MakeWorld, reason: str) -> None:
    async with make_world() as w:
        if reason == "report_done":
            await w.db.execute(
                text("UPDATE bug_reports SET status='done', handled_at=now() WHERE id=:i"),
                {"i": w.report_id},
            )
        else:
            await w.db.execute(
                text("UPDATE bug_reports SET created_at = now() - interval '2 hours' WHERE id=:i"),
                {"i": w.report_id},
            )
        await w.db.commit()
        resp = await w.upload()
        assert resp.status_code == 403
        assert _error(resp)["code"] == "attachment_upload_closed"
        assert _error(resp)["details"] == {"reason": reason}
        assert w.fake.count("create") == 0


async def test_upload_window_is_configurable(make_world: MakeWorld) -> None:
    async with make_world(bug_report_attachment_window_seconds=60) as w:
        await w.db.execute(
            text("UPDATE bug_reports SET created_at = now() - interval '90 seconds' WHERE id=:i"),
            {"i": w.report_id},
        )
        await w.db.commit()
        resp = await w.upload()
        assert _error(resp)["details"] == {"reason": "window_expired"}


async def test_upload_requires_cookie_401(make_world: MakeWorld) -> None:
    async with make_world() as w:
        resp = await w.upload(headers={"Cookie": ""})
        assert resp.status_code == 401
        assert w.fake.count("create") == 0


async def test_upload_unverified_403(make_world: MakeWorld) -> None:
    async with make_world() as w:
        await w.db.execute(
            text("UPDATE users SET email_verified_at = NULL WHERE id=:i"), {"i": w.user_id}
        )
        await w.db.commit()
        resp = await w.upload()
        assert resp.status_code == 403
        assert _error(resp)["code"] == "email_unverified"


@pytest.mark.parametrize("case", ["no_origin", "foreign_origin", "no_x_mm_app", "admin_app"])
async def test_upload_origin_and_app_header_403(make_world: MakeWorld, case: str) -> None:
    async with make_world() as w:
        kwargs: dict[str, Any] = {
            "no_origin": {"origin": None},
            "foreign_origin": {"origin": "https://evil.mmlabs.online"},
            "no_x_mm_app": {"headers": {"X-MM-App": ""}},
            "admin_app": {"app_key": "admin"},
        }[case]
        resp = await w.upload(**kwargs)
        assert resp.status_code in (400, 403, 422), resp.text
        assert w.fake.count("create") == 0
        assert await w.rows() == []


@pytest.mark.parametrize("which", ["report_id", "account_id"])
async def test_upload_bad_ids_422(make_world: MakeWorld, which: str) -> None:
    async with make_world() as w:
        resp = await w.upload(**{which: "not-a-uuid"})
        assert resp.status_code == 422
        assert _error(resp)["code"] == "validation_error"


# --- rate limits and slots -----------------------------------------------------------------------


async def test_upload_rate_limited_ip(make_world: MakeWorld) -> None:
    async with make_world(bug_report_attachment_rate_limit=1) as w:
        assert (await w.upload()).status_code == 201
        resp = await w.upload()
        assert resp.status_code == 429
        assert _error(resp)["code"] == "rate_limited"
        assert "retry-after" in resp.headers


async def test_upload_rate_limited_user_counts_rejected_attempts(make_world: MakeWorld) -> None:
    async with make_world(bug_report_attachment_user_rate_limit=2) as w:
        assert (await w.upload(b"<html></html>", "text/html")).status_code == 415
        assert (await w.upload(b"x" * 20, PNG_TYPE)).status_code == 415
        resp = await w.upload()
        assert resp.status_code == 429
        assert _error(resp)["code"] == "rate_limited"
        assert w.fake.count("create") == 0


async def test_upload_busy_429(make_world: MakeWorld) -> None:
    async with make_world() as w:
        slots = asyncio.Semaphore(1)
        await slots.acquire()
        w.app.state.attachment_upload_slots = slots
        resp = await w.upload()
        assert resp.status_code == 429
        assert _error(resp)["code"] == "attachment_upload_busy"
        assert resp.headers["retry-after"] == "5"
        assert w.fake.count("create") == 0
        slots.release()
        assert (await w.upload()).status_code == 201  # the slot is free again


async def test_upload_releases_slot_after_failure(make_world: MakeWorld) -> None:
    async with make_world() as w:
        slots = w.app.state.attachment_upload_slots
        before = slots._value
        await w.upload(b"<html>not an image</html>", PNG_TYPE)
        w.fake.queue("create", 503)
        await w.upload()
        assert slots._value == before


# --- Drive off or failing ------------------------------------------------------------------------


async def test_upload_drive_disabled_503(make_world: MakeWorld) -> None:
    async with make_world() as w:
        w.app.state.attachment_store = DisabledAttachmentStore()
        resp = await w.upload()
        assert resp.status_code == 503
        assert _error(resp)["code"] == "attachment_storage_unavailable"
        assert _error(resp)["details"] == {"retryable": False}
        assert "retry-after" not in resp.headers
        assert await w.rows() == []
        assert (await w.db.get(BugReport, w.report_id)) is not None


DRIVE_FAILURES: dict[str, tuple[Any, bool]] = {
    "unavailable": (503, True),
    "rate": (httpx.Response(403, json=error_body("userRateLimitExceeded")), True),
    "quota": (httpx.Response(403, json=error_body("storageQuotaExceeded")), False),
    "auth": (401, False),
}


@pytest.mark.parametrize("case", list(DRIVE_FAILURES))
async def test_upload_drive_failure_503(make_world: MakeWorld, case: str) -> None:
    outcome, retryable = DRIVE_FAILURES[case]
    async with make_world() as w:
        w.fake.queue("create", outcome)
        resp = await w.upload()
        assert resp.status_code == 503, resp.text
        assert _error(resp)["code"] == "attachment_storage_unavailable"
        assert _error(resp)["details"] == {"retryable": retryable}
        assert ("retry-after" in resp.headers) is retryable
        assert await w.rows() == []
        assert w.fake.count("create") == 1  # creates are never retried
        report = await w.db.get(BugReport, w.report_id)
        assert report is not None and report.status == "open"


class FailingAdd:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def add(self: Any, row: Any) -> Any:
            raise RuntimeError("injected insert failure")

        monkeypatch.setattr(BugReportAttachmentRepository, "add", add)


async def test_upload_db_failure_deletes_drive_file(
    make_world: MakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with make_world() as w:
        FailingAdd(monkeypatch)
        resp = await w.upload()
        assert resp.status_code == 500
        assert w.fake.count("create") == 1
        assert w.fake.count("delete") == 1
        assert w.drive_attachments() == []
        assert await w.rows() == []


class ClosingStore:
    """Staff marks the report done while the Drive call is in flight."""

    configured = True

    def __init__(self, inner: AttachmentStore, w: World) -> None:
        self.inner = inner
        self.w = w

    async def create(self, **kwargs: Any) -> str:
        file_id = await self.inner.create(**kwargs)
        await self.w.db.execute(
            text("UPDATE bug_reports SET status='done', handled_at=now() WHERE id=:i"),
            {"i": self.w.report_id},
        )
        await self.w.db.commit()
        return file_id

    async def delete(self, file_id: str) -> None:
        await self.inner.delete(file_id)


async def test_upload_report_closed_between_check_and_insert(make_world: MakeWorld) -> None:
    async with make_world() as w:
        w.app.state.attachment_store = ClosingStore(w.app.state.attachment_store, w)
        resp = await w.upload()
        assert resp.status_code == 403
        assert _error(resp)["code"] == "attachment_upload_closed"
        assert w.drive_attachments() == []  # the file was deleted again
        assert await w.rows() == []


async def test_upload_rotated_cookie_survives_post_commit_error(make_world: MakeWorld) -> None:
    async with make_world() as w:
        await shift(w.db, "device_sessions", "rotated_at", w.device_id, "-25 hours")
        await w.db.commit()
        w.fake.queue("create", 503)
        failed = await w.upload()
        assert failed.status_code == 503
        morsel = parse_set_cookie(failed)
        assert morsel is not None and morsel.value != w.raw
        assert "httponly" in failed.headers["set-cookie"].lower()
        again = await w.upload(raw=morsel.value)
        assert again.status_code == 201  # the new value works


async def test_upload_error_before_commit_sets_no_cookie(make_world: MakeWorld) -> None:
    async with make_world() as w:
        await shift(w.db, "device_sessions", "rotated_at", w.device_id, "-25 hours")
        await w.db.commit()
        resp = await w.upload(report_id=uuid.uuid4())  # denied in tx 1: the rotation rolls back
        assert resp.status_code == 403
        assert parse_set_cookie(resp) is None


# --- no leaks ------------------------------------------------------------------------------------


async def test_drive_ids_never_exposed(make_world: MakeWorld) -> None:
    async with make_world() as w:
        ok = await w.upload()
        (file_id,) = w.drive_attachments()
        w.fake.queue("create", 503)
        failed = await w.upload()
        denied = await w.upload(report_id=uuid.uuid4())
        detail = await w.client.get(f"/api/v1/bug-reports/{w.report_id}")
        dl = await w.client.get(
            f"/api/v1/bug-reports/{w.report_id}/attachments/{ok.json()['id']}/content"
        )
        for resp in (ok, failed, denied, detail, dl):
            assert file_id not in ("" if resp is dl else resp.text)
            assert file_id not in str(dict(resp.headers))
        (row,) = await w.rows()
        assert row.drive_file_id == file_id


async def test_no_content_or_ids_in_logs_and_audit(
    make_world: MakeWorld, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("app.modules.bug_reports.service", "app.modules.attachments.drive"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    caplog.set_level(logging.DEBUG)
    async with make_world() as w:
        resp = await w.upload(
            headers={"Content-Disposition": 'attachment; filename="leaky-name.png"'}
        )
        assert resp.status_code == 201
        w.fake.queue("create", 503)
        await w.upload(headers={"Content-Disposition": 'attachment; filename="leaky-name.png"'})
        (file_id,) = w.drive_attachments()
        audits = [
            str(r.changes) + str(r.actor_label) for r in (await w.db.scalars(select(AuditLog)))
        ]
    assert "bug_report_attachment_added" in caplog.text  # the logger was live
    haystack = caplog.text + " ".join(audits)
    for needle in (file_id, SENTINEL.decode(), "leaky-name", "image/png"):
        assert needle not in haystack, needle


async def test_cors_preflight_allows_upload(make_world: MakeWorld) -> None:
    async with make_world() as w:
        resp = await w.client.options(
            w.url(),
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-mm-app",
            },
        )
        assert resp.status_code == 204
        assert resp.headers["access-control-allow-origin"] == ORIGIN
        assert resp.headers["access-control-allow-credentials"] == "true"
